import argparse
from datetime import datetime, timedelta
import os

import pendulum
import requests
from notion_helper import NotionHelper

from weread_api import WeReadApi
import utils
from config import book_properties_type_dict, tz
from retrying import retry

TAG_ICON_URL = "https://www.notion.so/icons/tag_gray.svg"
USER_ICON_URL = "https://www.notion.so/icons/user-circle-filled_gray.svg"
BOOK_ICON_URL = "https://www.notion.so/icons/book_gray.svg"

rating = {"poor": "⭐️", "fair": "⭐️⭐️⭐️", "good": "⭐️⭐️⭐️⭐️⭐️"}

@retry(stop_max_attempt_number=3, wait_fixed=5000)
def get_douban_url(isbn):
    print(f"get_douban_url {isbn} ")
    params = {"query": isbn, "page": "1", "category": "book"}
    r = requests.get("https://neodb.social/api/catalog/search", params=params)
    if r.ok:
        books = r.json().get("data")
        if books is None or len(books) == 0:
            return None
        results = list(filter(lambda x: x.get("isbn") == isbn, books))
        if len(results) == 0:
            return None
        result = results[0]
        urls = list(
            filter(
                lambda x: x.get("url").startswith("https://book.douban.com"),
                result.get("external_resources", []),
            )
        )
        if len(urls) == 0:
            return None
        return urls[0].get("url")


def insert_book_to_notion(books, index, bookId):
    """插入Book到Notion"""
    book = {}
    if bookId in archive_dict:
        book["书架分类"] = archive_dict.get(bookId)
    if bookId in notion_books:
        book.update(notion_books.get(bookId))
    bookInfo = weread_api.get_bookinfo(bookId)
    if bookInfo is not None:
        book.update(bookInfo)
    readInfo = weread_api.get_read_info(bookId)
    readInfo.update(readInfo.get("readDetail", {}))
    readInfo.update(readInfo.get("bookInfo", {}))
    book.update(readInfo)
    book["阅读进度"] = (
        100 if (book.get("markedStatus") == 4) else book.get("readingProgress", 0)
    ) / 100
    markedStatus = book.get("markedStatus")
    status = "想读"
    if markedStatus == 4:
        status = "已读"
    elif book.get("readingTime", 0) >= 60:
        status = "在读"
    book["阅读状态"] = status
    book["阅读时长"] = book.get("readingTime")
    book["阅读天数"] = book.get("totalReadDay")
    book["评分"] = book.get("newRating")
    if book.get("newRatingDetail") and book.get("newRatingDetail").get("myRating"):
        book["我的评分"] = rating.get(book.get("newRatingDetail").get("myRating"))
    elif status == "已读":
        book["我的评分"] = "未评分"
    date = None
    if book.get("finishedDate"):
        date = book.get("finishedDate")
    elif book.get("lastReadingDate"):
        date = book.get("lastReadingDate")
    elif book.get("readingBookDate"):
        date = book.get("readingBookDate")
    book["时间"] = date
    book["开始阅读时间"] = book.get("beginReadingDate")
    book["最后阅读时间"] = book.get("lastReadingDate")
    cover = book.get("cover").replace("/s_", "/t7_")
    if not cover and not cover.strip() and not cover.startswith("http"):
        cover = BOOK_ICON_URL
    if bookId not in notion_books:
        isbn = book.get("isbn")
        if isbn and isbn.strip():
            douban_url = get_douban_url(isbn)
            if douban_url:
                book["douban_url"] = douban_url
        book["书名"] = book.get("title")
        book["BookId"] = book.get("bookId")
        book["ISBN"] = book.get("isbn")
        book["链接"] = utils.get_weread_url(bookId)
        book["简介"] = book.get("intro")
        book["作者"] = [
            notion_helper.get_relation_id(
                x, notion_helper.author_database_id, USER_ICON_URL
            )
            for x in book.get("author").split(" ")
        ]
        if book.get("categories"):
            book["分类"] = [
                notion_helper.get_relation_id(
                    x.get("title"), notion_helper.category_database_id, TAG_ICON_URL
                )
                for x in book.get("categories")
            ]
    properties = utils.get_properties(book, book_properties_type_dict)
    if book.get("时间"):
        notion_helper.get_date_relation(
            properties,
            pendulum.from_timestamp(book.get("时间"), tz="Asia/Shanghai"),
        )

    print(f"::notice::正在插入《{book.get('title')}》,一共{len(books)}本，当前是第{index+1}本。")
    parent = {"database_id": notion_helper.book_database_id, "type": "database_id"}
    result = None
    if bookId in notion_books:
        result = notion_helper.update_page(
            page_id=notion_books.get(bookId).get("pageId"),
            properties=properties,
            cover=utils.get_icon(cover),
        )
    else:
        result = notion_helper.create_book_page(
            parent=parent,
            properties=properties,
            icon=utils.get_icon(cover),
        )
    page_id = result.get("id")
    if book.get("readDetail") and book.get("readDetail").get("data"):
        data = book.get("readDetail").get("data")
        data = {item.get("readDate"): item.get("readTime") for item in data}
        insert_read_data(page_id, data)


def insert_read_data(page_id, readTimes):
    readTimes = dict(sorted(readTimes.items()))
    filter = {"property": "书架", "relation": {"contains": page_id}}
    results = notion_helper.query_all_by_book(notion_helper.read_database_id, filter)
    for result in results:
        timestamp = result.get("properties").get("时间戳").get("number")
        duration = result.get("properties").get("时长").get("number")
        id = result.get("id")
        if timestamp in readTimes:
            value = readTimes.pop(timestamp)
            if value != duration:
                insert_to_notion(
                    page_id=id,
                    timestamp=timestamp,
                    duration=value,
                    book_database_id=page_id,
                )
    for key, value in readTimes.items():
        insert_to_notion(None, int(key), value, page_id)


def insert_to_notion(page_id, timestamp, duration, book_database_id):
    parent = {"database_id": notion_helper.read_database_id, "type": "database_id"}
    properties = {
        "标题": utils.get_title(
            pendulum.from_timestamp(timestamp, tz=tz).to_date_string()
        ),
        "日期": utils.get_date(
            start=pendulum.from_timestamp(timestamp, tz=tz).format(
                "YYYY-MM-DD HH:mm:ss"
            )
        ),
        "时长": utils.get_number(duration),
        "时间戳": utils.get_number(timestamp),
        "书架": utils.get_relation([book_database_id]),
    }
    if page_id is not None:
        notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        notion_helper.client.pages.create(
            parent=parent,
            icon=utils.get_icon("https://www.notion.so/icons/target_red.svg"),
            properties=properties,
        )


if __name__ == "__main__":
    weread_api = WeReadApi()
    notion_helper = NotionHelper()
    notion_books = notion_helper.get_all_book()
    bookshelf_books = weread_api.get_bookshelf()

    # 打印获取到的书架数据
    print("Bookshelf Books:", bookshelf_books)

    # 获取名为 "ll的书架" 的书籍
    ll_bookshelf = next((shelf for shelf in bookshelf_books.get("archive", []) if shelf.get("name") == "ll的书架"), None)

    if ll_bookshelf:
        print("Selected Bookshelf:", ll_bookshelf)  # 打印选中的书架信息

        bookProgress = ll_bookshelf.get("bookProgress", [])
        bookProgress = {book.get("bookId"): book for book in bookProgress}
        archive_dict = {bookId: ll_bookshelf.get("name") for bookId in ll_bookshelf.get("bookIds", [])}
    else:
        bookProgress = {}
        archive_dict = {}

    print("Book Progress:", bookProgress)
    print("Archive Dict:", archive_dict)

    # 获取 "ll的书架" 中不需要同步的书籍
    not_need_sync = []
    for key, value in notion_books.items():
        if (
            (key not in bookProgress or value.get("readingTime") == bookProgress.get(key, {}).get("readingTime"))
            and (archive_dict.get(key) == value.get("category"))
            and (value.get("cover") is not None)
            and (
                value.get("status") != "已读"
                or (value.get("status") == "已读" and value.get("myRating"))
            )
        ):
            not_need_sync.append(key)

    # 获取 "ll的书架" 中的书籍
    ll_bookshelf_books = set(ll_bookshelf.get("bookIds", []))
    # 提取 "ll的书架" 中的书籍，并去重
    # 获取所有笔记本中的书籍列表
    notebooks = weread_api.get_notebooklist()
    # 仅同步 "ll的书架" 中的书籍
    books = list(ll_bookshelf_books - set(not_need_sync))
    # 检查重复情况
    # 打印书架中的所有书籍
    print("All Books in 'll的书架':", ll_bookshelf_books)
    print(f"去重后的书籍数量: {len(books)}")
    print("Books to Sync:", books)
    print("Notion Books:", notion_books)
    print("Book Progress:", bookProgress)
    for key, value in notion_books.items():
        print(f"Checking book {key}:")
        print(f"  Reading Time: {value.get('readingTime')} == {bookProgress.get(key, {}).get('readingTime')}")
        print(f"  Archive Dict Category: {archive_dict.get(key)} == {value.get('category')}")
        print(f"  Cover: {value.get('cover')}")
        print(f"  Status: {value.get('status')} and My Rating: {value.get('myRating')}")

    # 插入书籍到 Notion
    for index, bookId in enumerate(books):
        insert_book_to_notion(books, index, bookId)
