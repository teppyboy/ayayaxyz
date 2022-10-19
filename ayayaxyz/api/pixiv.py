import asyncio
import logging
import sys
import time
import requests
from io import BytesIO
from pathlib import Path, PurePath
from random import randint
from threading import Thread
from urllib.parse import urlparse
from typing import Optional

from appdirs import user_cache_dir
from flask import send_file, Flask
from pixivpy3 import *


class PixivException(Exception):
    """Base class for all Pixiv errors"""

    pass


class PixivLoginError(PixivException):
    """Raised when login Pixiv failed with error"""

    pass


class PixivGetIllustrationFailed(PixivException):
    """Raised when get illust information failed"""

    pass


class PixivDownloadError(PixivException):
    """Raised when download illustration failed"""

    pass


class PixivSearchError(PixivException):
    """Raised when searching for image failed"""

    pass


class PixivSearchRelatedError(PixivSearchError):
    """Raised when searching for related image failed"""

    pass


class Pixiv:
    def __init__(self):
        self._pixiv = AppPixivAPI()
        self._session = requests.Session()
        self._path = Path("./pixiv")
        self._logger = logging.getLogger("ayayaxyz.api.pixiv")
        if not self._path.is_dir():
            self._path = Path(
                user_cache_dir("ayayaxyz-telegram", "tretrauit")
            ).joinpath("pixiv-api")
            self._path.mkdir(parents=True, exist_ok=True)
        self._logger.info("Pixiv API cache path: {}".format(self._path))
        # Tag translation
        self._pixiv.set_accept_language("en-us")
        # Login workaround
        self._login_thread = None

    def login_token(self, refresh_token):
        if self._login_thread:
            return

        def _login():
            while True:
                try:
                    self._pixiv.auth(refresh_token=refresh_token)
                except PixivError as e:
                    raise PixivLoginError(e)
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
            raise PixivLoginError(e)
        self.login_token(refresh_token=login_rsp.get("refresh_token"))

    async def _download_illust(self, url, path: Optional[Path] = None) -> (BytesIO | None, str):
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

    async def get_illust_from_id(self, illust_id: int):
        try:
            illust = (await asyncio.to_thread(self._pixiv.illust_detail, illust_id))[
                "illust"
            ]
        except KeyError as e:
            raise PixivGetIllustrationFailed(
                "Failed to get illust with error: {}".format(e)
            )
        return illust

    @staticmethod
    async def get_illust_download_url(
        illust, pictures: list[int] | None = None, quality="original"
    ):
        print("Fetching {}".format(illust["id"]))
        if illust["meta_single_page"] == {}:
            print("Multiple pages illustration.")
            images = []
            for index, page in enumerate(illust["meta_pages"]):
                if pictures is None or pictures == [] or index in pictures:
                    images.append(page["image_urls"][quality])
            return images
        print("Single page illustration.")
        if quality == "original":
            illust_dl = illust["meta_single_page"]["original_image_url"]
        else:
            illust_dl = illust["image_urls"][quality]
        return [illust_dl]

    async def download_illust(
        self,
        illust,
        pictures: list[int] | None = None,
        quality="original",
        limit: int | None = None,
        to_url: bool | None = False,
    ):
        if limit is not None and pictures is not None and len(pictures) > limit:
            raise PixivDownloadError(
                "Images list exceeded limit ({} while limit is {})".format(
                    len(pictures), limit
                )
            )
        print("Fetching {}".format(illust["id"]))
        if illust["meta_single_page"] == {}:
            print("Multiple pages illustration.")
            if limit is not None:
                if not pictures and len(illust["meta_pages"]) > limit:
                    raise PixivDownloadError(
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
                    raise PixivDownloadError(e)
            return images
        print("Single page illustration.")
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
                raise PixivDownloadError(e)
        return images

    @staticmethod
    def _get_raw_tags(image):
        tags = []
        for tag in image["tags"]:
            tags.append(tag["name"])
        return tags

    def _image_from_tag_matching(
        self,
        images,
        tags: list[str] | set[str] | None = None,
        exclude_tags: list[str] | set[str] | None = None,
    ):
        print("Using hacky algorithm...")
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
            print("prev img", searched_images)
            if len(searched_images) == len(images):
                raise PixivSearchError(
                    "Couldn't find any images matching provided keywords"
                )
            while True:
                print("images size", len(images))
                image_count = randint(0, len(images) - 1)
                print("image count", image_count)
                if image_count not in searched_images:
                    break
            print("image index", image_count)
            searched_images.append(image_count)
            current_image = images[image_count]
            print(self._get_raw_tags(current_image))
            r18_image = "R-18" in self._get_raw_tags(current_image)
            if r18_image and "r-18" not in tags:
                print("A r-18 image but we don't want r-18")
                continue
            elif not r18_image and "r-18" in tags:
                print("Not a r-18 image but we wanted r-18")
                continue
            print("beginning tag partial matching")
            found_tags = set()
            found_bl_tags = set()
            # Found tags for joined words.
            found_tags_jw = set()
            for tag in current_image["tags"]:
                if exclude_tags:
                    for kw in exclude_tags:
                        kw_set = set(kw.split(" "))
                        print("parsing tag:", tag["name"], tag["translated_name"])
                        print("current blacklist tag:", kw_set)
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
            print("final tags", found_tags, tags)
            if tags == found_tags:
                if found_bl_tags and found_bl_tags.issubset(exclude_tags):
                    print("illust contains blacklisted words, not using")
                    continue
                image = current_image
        print("found image we maybe looking for")
        return image

    async def related_illust(
        self, illust_id: int, tags: list[str] | set[str] | None = None, recurse: int | None = None
    ):
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
        try:
            result = (await asyncio.to_thread(self._pixiv.illust_related, illust_id))[
                "illusts"
            ]
        except KeyError as e:
            raise PixivSearchRelatedError(e)

        try:
            image = self._image_from_tag_matching(
                result, tags=tags, exclude_tags=exclude_tags
            )
        except PixivSearchError as e:
            raise PixivSearchRelatedError(e)
        if image["id"] == illust_id:
            raise PixivSearchRelatedError(
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
        tags_orig = tags
        exclude_tags = set(x for x in tags if x.startswith("-"))
        tags = set(tags) - exclude_tags
        print(tags, exclude_tags)
        filter = ""
        # if "R-18" not in tags and "r-18" not in tags:
        #     # Be safe here, no NSFW ;)
        #     filter = "for_ios"
        attempt = 0
        image = None
        while image is None and attempt < max_attempt:
            print("Search attempt", attempt)
            if sort is None:
                sort = ["date_desc", "popular_desc"][randint(0, 1)]
            print(sort)
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
                    related_image = None
                    related_attempt = 0
                    while (
                        related_image is None and related_attempt < max_related_attempt
                    ):
                        try:
                            related_image = await self.related_illust(
                                image["id"], tags=tags_orig
                            )
                        except PixivSearchRelatedError:
                            pass
                        related_attempt += 1
                    if related_image:
                        print("Found related image matches our query")
                        image = related_image
            except (KeyError, PixivSearchError):
                pass
            attempt += 1
        if image is None:
            raise PixivSearchError("No images matches specified tags")
        return image

    @staticmethod
    def _translate_tag(img, tag) -> str:
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
            tl_tag = self._translate_tag(
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

    async def translate_tags(self, tags: list[str]) -> list[str]:
        """
        Experimental tags translation using Pixiv Ajax API
        """
        tl_tags = []
        for tag in tags:
            print("Translating", tag)
            exclude_tag = False
            if tag.startswith("-"):
                tag = tag[1:]
                exclude_tag = True
            tag_name = tag
            tag = tag.lower()
            tag_kw = set(tag.split(" "))
            r = self._session.get(
                "https://www.pixiv.net/rpc/cps.php",
                params={"keyword": tag.split(" ")[0], "lang": "en"},
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
            if exclude_tag:
                tag_name = "-" + tag
            tl_tags.append(tag_name)
        print(tl_tags)
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
            raise PixivSearchError("No tags specified.")
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

        @app.route(route + "/<path:url>", methods=["GET"])
        async def pixiv_api(url):
            logger.info("Got a /pixiv request")
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
