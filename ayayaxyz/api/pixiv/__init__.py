import asyncio
import logging
import sys
import time
import requests_cache
from zipfile import ZipFile
from io import BytesIO
from pathlib import Path, PurePath
from random import randint
from threading import Thread
from urllib.parse import urlparse

from appdirs import user_cache_dir
from flask import send_file, Flask, request
from pixivpy3 import *

from .exceptions import *


class Pixiv:
    def __init__(self):
        self._pixiv = ByPassSniApi()
        # self._pixiv.require_appapi_hosts()
        self._session = requests_cache.CachedSession("ayayaxyz-api-pixiv")
        self._path = Path("./pixiv-cache")
        self._logger = logging.getLogger("ayayaxyz.api.pixiv")
        if not self._path.is_dir():
            self._path = Path(
                user_cache_dir("ayayaxyz-telegram", "tretrauit")
            ).joinpath("pixiv-api")
            self._path.mkdir(parents=True, exist_ok=True)
        self._ugoira_cache = self._path.joinpath("ugoira-cache")
        self._ugoira_cache.mkdir(exist_ok=True)
        self._logger.info("Pixiv API cache path: {}".format(self._path))
        # Tag translation
        self._pixiv.set_accept_language("en-us")
        # Login workaround
        self._login_thread = None

    def login_token(self, refresh_token: str):
        if self._login_thread:
            return

        def _login():
            while True:
                try:
                    self._pixiv.auth(refresh_token=refresh_token)
                except PixivError as e:
                    raise LoginError(e)
                time.sleep(randint(900, 1200))

        self._login_thread = Thread(target=_login)
        self._login_thread.daemon = True
        self._login_thread.start()
        time.sleep(1)

    def login(self, username: str, password: str):
        if self._login_thread:
            return
        try:
            if "gppt" not in sys.modules:
                from gppt import GetPixivToken

            login_rsp = GetPixivToken().login(
                headless=True, user=username, pass_=password
            )
        except Exception as e:
            raise LoginError(e)
        self.login_token(refresh_token=login_rsp.get("refresh_token"))

    async def _download_illust(
        self, url: str, path: Path | None = None
    ) -> tuple[BytesIO | None, str]:
        if path:
            path = self._path.joinpath(path).parent
            path.mkdir(parents=True, exist_ok=True)
            image_bytes = None
        else:
            image_bytes = BytesIO()
        image_name = PurePath(url).name
        await asyncio.to_thread(
            self._pixiv.download, url, path=str(path), fname=image_bytes
        )
        return image_bytes, image_name

    async def _download_ugoira(self, url: str) -> Path:
        file_name = PurePath(url).name
        file_stem = PurePath(url).stem
        await asyncio.to_thread(self._pixiv.download, url, path=str(self._ugoira_cache))
        file_path = self._ugoira_cache.joinpath(file_name)
        extract_path = self._ugoira_cache.joinpath(file_stem)
        extract_path.mkdir(exist_ok=True)
        with ZipFile(file_path, "r") as f:
            f.extractall(extract_path)
        return extract_path

    async def _convert_ugoira_to_webm(self, ugoira_path: Path, fps: float) -> Path:
        converted = self._ugoira_cache.joinpath("converted")
        converted.mkdir(exist_ok=True)
        out = converted.joinpath(ugoira_path.with_suffix(".webm").name)
        args = ["ffmpeg", "-y", "-c:v", "libvpx-vp9"]
        for image in ugoira_path.iterdir():
            args += ["-i", f"{image}"]
        args += ["-r", f"{int(fps)}", f"{out}"]
        proc = await asyncio.create_subprocess_exec(*args)
        retcode = await proc.wait()
        if retcode != 0:
            raise RuntimeError("Convert error")
        return out

    async def get_video_from_ugoira(self, illust_id: int, ugoira: dict = None) -> Path:
        logger = self._logger.getChild("get_video_from_ugoira")
        if not ugoira:
            ugoira = await self.get_ugoira_from_id(illust_id=illust_id)
        logger.debug(ugoira)
        frm_delay = 0
        for frame in ugoira["body"]["frames"]:
            frm_delay += frame["delay"]
        fps = 1000 / (frm_delay / len(ugoira["body"]["frames"]))
        dl_path = await self._download_ugoira(ugoira["body"]["originalSrc"])
        video = await self._convert_ugoira_to_webm(dl_path, fps=fps)
        return video

    async def get_ugoira_from_id(self, illust_id: int) -> dict:
        ugoira: dict = self._pixiv.no_auth_requests_call(
            "GET", "https://www.pixiv.net/ajax/illust/{}/ugoira_meta".format(illust_id)
        ).json()
        if ugoira["error"]:
            if ugoira["message"] == "The ID you provided is not an Ugoira":
                raise NotAnUgoiraError(ugoira["message"])
            raise GetUgoiraError(ugoira["message"])
        return ugoira

    async def get_ugoira(self, illust: dict) -> dict:
        if illust["type"] != "ugoira":
            raise NotAnUgoiraError("The ID you provided is not an Ugoira")
        return await self.get_ugoira_from_id(illust_id=illust["id"])

    async def get_illust_from_id(self, illust_id: int) -> dict:
        try:
            illust = (await asyncio.to_thread(self._pixiv.illust_detail, illust_id))[
                "illust"
            ]
        except KeyError as e:
            raise GetIllustrationError("Failed to get illust with error: {}".format(e))
        return illust

    async def get_illust_download_url(
        self, illust: dict, pictures: list[int] | None = None, quality: str = "original"
    ) -> list[str]:
        logger: logging.Logger = self._logger.getChild("get_illust_download_url")
        logger.debug("{}".format(str(illust)))
        logger.debug("Fetching {}".format(illust["id"]))
        if illust["meta_single_page"] == {}:
            logger.debug("Multiple pages illustration.")
            images = []
            for index, page in enumerate(illust["meta_pages"]):
                if pictures is None or pictures == [] or index in pictures:
                    images.append(page["image_urls"][quality])
            return images
        logger.debug("Single page illustration.")
        if quality == "original":
            illust_dl = illust["meta_single_page"]["original_image_url"]
        else:
            illust_dl = illust["image_urls"][quality]
        return [illust_dl]

    async def download_illust(
        self,
        illust,
        pictures: list[int] | None = None,
        quality: str = "original",
        limit: int | None = None,
        to_url: bool | None = False,
    ):
        if limit is not None and pictures is not None and len(pictures) > limit:
            raise DownloadError(
                "Images list exceeded limit ({} while limit is {})".format(
                    len(pictures), limit
                )
            )
        logger: logging.Logger = self._logger.getChild("download_illust")
        logger.debug("Fetching {}".format(illust["id"]))
        if illust["meta_single_page"] == {}:
            logger.debug("Multiple pages illustration.")
            if limit is not None:
                if not pictures and len(illust["meta_pages"]) > limit:
                    raise DownloadError(
                        "Images exceeded limit ({} while limit is {})".format(
                            len(illust["meta_pages"]), limit
                        )
                    )
            images_job = []
            for index, page in enumerate(illust["meta_pages"]):
                if pictures == [] or index in pictures:
                    if to_url:
                        images_job.append(
                            (
                                page["image_urls"][quality],
                                PurePath(page["image_urls"][quality]).name,
                            )
                        )
                    else:
                        images_job.append(
                            self._download_illust(page["image_urls"][quality])
                        )
            if to_url:
                images = images_job
            else:
                try:
                    images = await asyncio.gather(*images_job)
                except PixivError as e:
                    raise DownloadError(e)
            return images
        logger.debug("Single page illustration.")
        if quality == "original":
            illust_dl = illust["meta_single_page"]["original_image_url"]
        else:
            illust_dl = illust["image_urls"][quality]
        if to_url:
            images = [(illust_dl, PurePath(illust_dl).name)]
        else:
            try:
                images = [await self._download_illust(illust_dl)]
            except PixivError as e:
                raise DownloadError(e)
        return images

    @staticmethod
    def get_raw_tags(image) -> list[str]:
        tags = []
        for tag in image["tags"]:
            tags.append(tag["name"])
        return tags

    @staticmethod
    def get_translated_tags(image) -> list[str]:
        tags = []
        for tag in image["tags"]:
            if tag["translated_name"] is None:
                tags.append(tag["name"])
                continue
            tags.append(tag["translated_name"])
        return tags

    def _image_from_tag_matching(
        self,
        images,
        tags: list[str] | set[str] | None = None,
        exclude_tags: list[str] | set[str] | None = None,
    ) -> dict:
        logger = self._logger.getChild("image_from_tag_matching")
        logger.debug("Using hacky image matching algorithm...")
        if tags is None:
            return images[randint(0, len(images) - 1)]
        if exclude_tags is None:
            exclude_tags = set()
        else:
            exclude_tags = set(x.lower()[1:] for x in exclude_tags)
        tags = set(x.lower() for x in tags)
        image = None
        searched_images = []
        while image is None:
            logger.debug(f"Previous image: {searched_images}")
            if len(searched_images) == len(images):
                raise SearchError("Couldn't find any images matching provided keywords")
            while True:
                logger.debug(f"Image array size: {len(images)}")
                image_count = randint(0, len(images) - 1)
                logger.debug(f"Selecting image: {image_count}")
                if image_count not in searched_images:
                    break
            logger.debug(f"Current selected image: {image_count}")
            searched_images.append(image_count)
            current_image = images[image_count]
            logger.debug(f"Raw tags: {self.get_raw_tags(current_image)}")
            r18_image = "R-18" in self.get_raw_tags(current_image)
            if r18_image and "r-18" not in tags:
                logger.debug("Image is a R-18 but we don't want R-18")
                continue
            elif not r18_image and "r-18" in tags:
                logger.debug("Image is not a R-18 image but we wanted R-18")
                continue
            logger.debug("Begin tag partial matching")
            found_tags = set()
            found_bl_tags = set()
            # Found tags for joined words.
            found_tags_jw = set()
            for tag in current_image["tags"]:
                logger.debug(f"Comparing with {tag['name']} ({tag['translated_name']})")
                if exclude_tags:
                    for kw in exclude_tags:
                        kw_set = set(kw.split(" "))
                        logger.debug(f"Current blacklist keyword: {kw_set}")
                        if tag["translated_name"] is not None and kw_set.issubset(
                            tag["translated_name"].lower().split(" ")
                        ):
                            found_bl_tags.add(kw)
                            continue
                        if kw_set.issubset(tag["name"].lower().split(" ")):
                            found_bl_tags.add(kw)
                            continue
                # Keyword in out specified tags
                for kw in tags:
                    # Normal search
                    kw_list = kw.split(" ")
                    kw_set = set(kw_list)
                    if tag["translated_name"] is not None and kw_set.issubset(
                        tag["translated_name"].lower().split(" ")
                    ):
                        found_tags.add(kw)
                        continue
                    if kw_set.issubset(tag["name"].lower().split(" ")):
                        found_tags.add(kw)
                        continue

                    # Conjoined words
                    kw_joined = "".join(kw_list)
                    kw_check_list = [kw_joined]
                    if len(kw_list) == 2:
                        kw_list[0], kw_list[1] = kw_list[1], kw_list[0]
                        kw_joined_swap = "".join(kw_list)
                        kw_check_list.append(kw_joined_swap)
                    if tag["name"].lower() in kw_check_list:
                        found_tags_jw.add(kw)
                        continue
                    if (
                        tag["translated_name"] is not None
                        and tag["translated_name"].lower() in kw_check_list
                    ):
                        found_tags_jw.add(kw)
                        continue

            found_tags.update(found_tags_jw)
            logger.debug(f"Final found tags & defined tags: {found_tags}, {tags}")
            if tags == found_tags:
                if found_bl_tags and found_bl_tags.issubset(exclude_tags):
                    logging.debug("Illust contains blacklisted words, not using")
                    continue
                image = current_image
        logger.debug("Found the illust we are maybe looking for")
        return image

    async def related_illust(
        self,
        illust_id: int,
        tags: list[str] | set[str] | None = None,
        recurse: int | None = None,
    ) -> dict:
        logger = self._logger.getChild("related_illust")
        if recurse is None:
            recurse = 0
        if recurse < 0:
            raise ValueError("Recurse must be greater than 0")
        if tags:
            exclude_tags = set(x for x in tags if x.startswith("-"))
            tags = set(tags) - exclude_tags
        else:
            exclude_tags = None
            tags = set()
        logger.debug(
            "ID: {}, tags: {}, exclude_tags: {}".format(illust_id, tags, exclude_tags)
        )
        try:
            result = (await asyncio.to_thread(self._pixiv.illust_related, illust_id))[
                "illusts"
            ]
            logger.debug("{}".format(result))
        except KeyError as e:
            raise SearchRelatedError(e)

        try:
            image = self._image_from_tag_matching(
                result, tags=tags, exclude_tags=exclude_tags
            )
        except SearchError as e:
            raise SearchRelatedError(e)
        if image["id"] == illust_id:
            raise SearchRelatedError(
                "Related image has the same ID as the original image."
            )
        if recurse == 0:
            return image
        return await self.related_illust(image["id"], tags, recurse - 1)

    async def _search_illust(
        self,
        tags: list[str] | set[str],
        related,
        sort,
        max_attempt,
        max_related_attempt,
    ):
        logger = self._logger.getChild("_search_illust")
        tags_orig = tags
        exclude_tags = set(x for x in tags if x.startswith("-"))
        tags = set(tags) - exclude_tags
        logger.debug("{} - {}".format(tags, exclude_tags))
        filter = ""
        # if "R-18" not in tags and "r-18" not in tags:
        #     # Be safe here, no NSFW ;)
        #     filter = "for_ios"
        attempt = 0
        image: dict | None = None
        while image is None and attempt < max_attempt:
            logger.debug("Search attempt: {}".format(attempt))
            if sort is None:
                sort = ["date_desc", "popular_desc"][randint(0, 1)]
            logger.debug(sort)
            try:
                result = (
                    await asyncio.to_thread(
                        self._pixiv.search_illust,
                        " ".join(tags),
                        sort=sort,
                        filter=filter,
                    )
                )["illusts"]
                image = self._image_from_tag_matching(
                    result, tags=tags, exclude_tags=exclude_tags
                )
                if related:
                    # Strict search
                    logger.debug("Searching for related image to our searched image...")
                    related_image = None
                    related_attempt = 0
                    while (
                        related_image is None and related_attempt < max_related_attempt
                    ):
                        try:
                            related_image = await self.related_illust(
                                image["id"], tags=tags_orig
                            )
                        except SearchRelatedError:
                            pass
                        related_attempt += 1
                    if related_image:
                        logger.debug("Found related image matches our query")
                        image = related_image
            except (KeyError, SearchError):
                pass
            attempt += 1
        if image is None:
            raise SearchError("No images matches specified tags")
        return image

    @staticmethod
    def _translate_tag_legacy(img, tag) -> str:
        print(img["tags"])
        for img_tag in img["tags"]:
            kw_set = set(tag.lower().split(" "))
            print(kw_set, img_tag["translated_name"])
            if img_tag["translated_name"] is not None and kw_set.issubset(
                img_tag["translated_name"].lower().split(" ")
            ):
                print("tl trigger")
                tag = img_tag["name"]
                break
        print("final translated tag", tag)
        return tag

    async def translate_tags_legacy(self, tags: list[str]) -> list[str]:
        tl_tags = []
        for tag in tags:
            print("Begin translate tag", tag)
            if tag.lower() == "r-18":
                tl_tags.append("R-18")
                continue
            tl_tag = self._translate_tag_legacy(
                img=await self._search_illust(
                    tags=[tag],
                    related=True,
                    sort="popular_desc",
                    max_attempt=1,
                    max_related_attempt=1,
                ),
                tag=tag,
            )
            print("Translated tag", tl_tag)
            tl_tags.append(tl_tag)
        print("Final translated tags", tl_tags)
        return tl_tags

    def _translate_tag(self, tag_kw: set[str], kw: str) -> str:
        tag_name: str | None = None
        r = self._session.get(
            "https://www.pixiv.net/rpc/cps.php",
            params={"keyword": kw, "lang": "en"},
            headers={
                "Referer": "https://www.pixiv.net/en/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36",
            },
        )
        r.raise_for_status()
        suggestions = r.json()
        for candidate in suggestions["candidates"]:
            if candidate["type"] != "tag_translation":
                continue
            if tag_kw.issubset(candidate["tag_translation"].lower().split(" ")):
                tag_name = candidate["tag_name"]
                break
        return tag_name

    async def translate_tags(self, tags: list[str]) -> list[str]:
        """
        Experimental tags translation using Pixiv Ajax API
        """
        logger: logging.Logger = self._logger.getChild("translate_tags")
        tl_tags: list[str] = []
        for tag in tags:
            if tag in ["R-18"]:
                logger.debug("Known tag: {}, not translating...".format(tag))
                tl_tags.append(tag)
                continue
            logger.debug("Translating tag: {}".format(tag))
            exclude_tag: bool = False
            if tag.startswith("-"):
                logger.debug("Exclude tag detected.")
                tag = tag[1:]
                exclude_tag = True
            tag_name: str = tag
            tag = tag.lower()
            tag_list: list[str] = tag.split(" ")
            tag_kw: set[str] = set(tag_list)
            if len(tag_list) > 1:
                tag_list[-1] = tag_list[-1][: int(len(tag_list[-1]) / 2)]
            px_search = " ".join(tag_list)
            logger.debug("Generated Pixiv search query: {}".format(px_search))
            tl_tag_name = self._translate_tag(tag_kw=tag_kw, kw=px_search)
            if tl_tag_name is None:
                logger.debug(
                    "Pixiv query search failed, using first word in tag to search..."
                )
                tl_tag_name = (
                    self._translate_tag(tag_kw=tag_kw, kw=tag_list[0]) or tag_name
                )
            if exclude_tag:
                tl_tag_name = "-" + tag
            logger.debug("Translated tag: {}".format(tl_tag_name))
            tl_tags.append(tl_tag_name)
        logger.debug("Final translated tags: {}".format(str(tl_tags)))
        return tl_tags

    async def search_illust(
        self,
        tags: list[str] | set[str],
        related=True,
        sort=None,
        max_attempt=None,
        max_related_attempt=None,
    ):
        if tags is None:
            raise SearchError("No tags specified.")
        max_attempt = 5 if not max_attempt else max_attempt
        max_related_attempt = 5 if not max_related_attempt else max_related_attempt
        return await self._search_illust(
            tags=tags,
            related=related,
            sort=sort,
            max_attempt=max_attempt,
            max_related_attempt=max_related_attempt,
        )

    async def search_download_illust(self, args: str, related=True):
        tags = [x.strip() for x in args.split(",")]
        page_list = [0]
        if "-p" in tags or "--all-pages" in tags:
            page_list = []
            try:
                tags.remove("-p")
            except ValueError:
                tags.remove("--all-pages")

        image = await self.search_illust(tags, related=related)
        return self.download_illust(image["id"], page_list)

    async def download_illust_to_cache(self, illust_url: str):
        parsed = urlparse(illust_url)
        # Remove the root "/" in the path from url.
        path = Path(parsed.path[1:])
        if not self._path.joinpath(path).is_file():
            await self._download_illust(url=illust_url, path=path)

    def flask_api(self, app: Flask, route: str | None = None):
        if not route:
            route = "/pixiv"

        logger = self._logger.getChild("flask-api")
        logger.info("Initializing pixiv Flask route...")

        @app.route(route + "/ugoira/video", methods=["GET"])
        async def pixiv_ugoira_api():
            logger.info("Got a /pixiv/ugoira/video request")
            px_id = request.args.get("id")
            if px_id is None:
                return "You need to pass an id query", 400
            try:
                video = await self.get_video_from_ugoira(px_id)
            except GetUgoiraError as e:
                return str(e), 500
            return send_file(path_or_file=Path("..").joinpath(video), etag=True)

        @app.route(route + "/id", methods=["GET"])
        async def pixiv_id_api():
            logger.info("Got a /pixiv/id request")
            try:
                px_id = int(request.args.get("id"))
            except ValueError as e:
                return str(e), 400
            if px_id is None:
                return "You need to pass an id query", 400
            px_page = int(request.args.get("page") or 0)
            px_quality = request.args.get("quality") or "original"
            pic_url = (
                await self.download_illust(
                    illust=await self.get_illust_from_id(px_id), pictures=[px_page], quality=px_quality, to_url=True
                )
            )[0][0]
            # Remove "https://""
            path = Path(pic_url[8:])
            # Workaround because Flask treat the module path as the base path instead
            full_path = Path("..").joinpath(self._path.joinpath(path))
            if not self._path.joinpath(path).is_file():
                logger.info("File doesn't exist, downloading...")
                await self._download_illust(url=pic_url, path=path)
            logger.info("Sending file...")
            return send_file(path_or_file=full_path, etag=True)

        @app.route(route + "/raw", methods=["GET"])
        async def pixiv_raw_api():
            logger.info("Got a /pixiv/raw request")
            url = request.args.get("url")
            if url is None:
                return "You need to pass an url query", 400
            parsed = urlparse(url)
            path = None
            if parsed.netloc != "":
                if parsed.netloc != "i.pximg.net":
                    return "Must be a i.pximg.net url", 400
            elif parsed.scheme == "":
                if not parsed.path.startswith("i.pximg.net"):
                    return "Must be a i.pximg.net url", 400
                url = "https://" + parsed.path
                path = parsed.path.removeprefix("i.pximg.net/")
            else:
                if not parsed.path.startswith("/i.pximg.net"):
                    return "Must be a i.pximg.net url", 400
                url = "https:/" + parsed.path
                path = parsed.path.removeprefix("/i.pximg.net/")
            if ".." in parsed.path:
                return "Illegal url provided", 403
            logger.info("Got file: {}".format(url))
            # Remove the root "/" in the path from url.
            if path is None:
                path = parsed.path[1:]
            path = Path(path)
            # Workaround because Flask treat the module path as the base path instead
            full_path = Path("..").joinpath(self._path.joinpath(path))
            if not self._path.joinpath(path).is_file():
                logger.info("File doesn't exist, downloading...")
                await self._download_illust(url=url, path=path)
            logger.info("Sending file...")
            return send_file(path_or_file=full_path, etag=True)
