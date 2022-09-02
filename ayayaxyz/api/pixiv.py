from pixivpy3 import *
from gppt import GetPixivToken
from pathlib import Path, PurePath
from io import BytesIO
from random import randint
from threading import Thread
import sys
import time
import asyncio
import argparse


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
        self._path = Path("./pixiv/")
        self._path.mkdir(parents=True, exist_ok=True)
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

    def login(self, username, password):
        if self._login_thread:
            return
        try:
            if not 'gppt' in sys.modules:
                from gppt import GetPixivToken
                self._gppt = GetPixivToken()
            login_rsp = self._gppt.login(headless=True, user=username, pass_=password)
        except Exception as e:
            raise PixivLoginError(e)
        self.login_token(refresh_token=login_rsp.get("refresh_token"))

    async def _download_illust(self, url):
        image_name = PurePath(url).name
        image_bytes = BytesIO()
        await asyncio.to_thread(self._pixiv.download, url, fname=image_bytes)
        return (image_bytes, image_name)

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

    async def download_illust(
        self, illust, pictures: list[int] = None, quality="original", limit: int = None
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
                    images_job.append(
                        self._download_illust(page["image_urls"][quality])
                    )
            images = await asyncio.gather(*images_job)
            return images
        print("Single page illustration.")
        if quality == "original":
            illust_dl = illust["meta_single_page"]["original_image_url"]
        else:
            illust_dl = illust["image_urls"][quality]
        return [await self._download_illust(illust_dl)]

    def _get_raw_tags(self, image):
        tags = []
        for tag in image["tags"]:
            tags.append(tag["name"])
        return tags

    def _image_from_tag_matching(self, images, tags: list[str] = None):
        print("Using hacky algorithm...")
        if tags is None:
            return images[randint(0, len(images) - 1)]
        tags = [x.lower() for x in tags]
        image = None
        searched_images = []
        while image is None:
            print("prev img", searched_images)
            if len(searched_images) == len(images):
                raise PixivSearchError(
                    "Couldn't find any images matching provided keywords"
                )
            while True:
                image_count = randint(0, len(images) - 1)
                if image_count not in searched_images:
                    break
            print("image index", image_count)
            searched_images.append(image_count)
            current_image = images[image_count]
            print(self._get_raw_tags(current_image))
            if not "r-18" in tags and "R-18" in self._get_raw_tags(current_image):
                print("not checking since we disabled r18 and this is r18 image")
                continue
            print("beginning tag partial matching")
            found_tags = set()
            for tag in current_image["tags"]:
                for kw in tags:
                    kw_set = set(kw.split(" "))
                    print("parsing tag:", tag["name"], tag["translated_name"])
                    print("current tag:", kw_set)
                    if tag["translated_name"] is not None and kw_set.issubset(
                        tag["translated_name"].lower().split(" ")
                    ):
                        found_tags.add(kw)
                    if kw_set.issubset(tag["name"].lower().split(" ")):
                        found_tags.add(kw)
            print("final tags", found_tags, set(tags))
            if set(tags) == found_tags:
                image = current_image
        print("found image we maybe looking for")
        return image

    async def related_illust(self, illust_id: int, tags: list[str] = None):
        try:
            result = (await asyncio.to_thread(self._pixiv.illust_related, illust_id))[
                "illusts"
            ]
        except KeyError as e:
            raise PixivSearchRelatedError(e)

        try:
            image = self._image_from_tag_matching(result, tags)
        except PixivSearchError as e:
            raise PixivSearchRelatedError(e)
        return image

    async def search_illust(self, tags: list[str], related=True, sort=None):
        if tags is None:
            raise PixivSearchError("No tags specified.")
        tags = [x.strip() for x in tags]
        print(tags)
        attempt = 0
        image = None
        sort_by = sort
        while image is None and attempt < 5:
            print("Search attempt", attempt)
            if sort is None:
                sort_by = ["date_desc", "popular_desc"][randint(0, 1)]
            print(sort_by)
            try:
                result = (
                    await asyncio.to_thread(
                        self._pixiv.search_illust,
                        " ".join(tags),
                        sort=sort_by,
                        filter="",
                    )
                )["illusts"]
                image = self._image_from_tag_matching(result, tags)
                if related:
                    # Strict search
                    related_image = None
                    related_attempt = 0
                    while related_image is None and related_attempt < 5:
                        try:
                            related_image = await self.related_illust(
                                image["id"], tags=tags
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

    async def search_download_illust(self, args: str, related=True):
        parser = argparse.ArgumentParser()
        parser.add_argument("tags", type=str, nargs="*")
        parser.add_argument("-p", "--all-pages", action="store_true")
        parsed = parser.parse_args(args.split(","))

        page_list = [0]
        if parsed.all_pages:
            page_list = []
        tags = parsed.tags
        image = self.search_illust(tags, related=related)
        return self.download_illust(image["id"], page_list)
