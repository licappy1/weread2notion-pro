import argparse
from datetime import datetime, timedelta
import os
import json
import pendulum
from dotenv import load_dotenv
from notion_helper import NotionHelper
from weread_api import WeReadApi
from utils import (
    format_date,
    get_date,
    get_icon,
    get_number,
    get_relation,
    get_title,
    get_embed,
)

load_dotenv()  # 加载 .env 文件中的环境变量
notion_token = os.getenv('NOTION_TOKEN')
def insert_to_notion(page_id, timestamp, duration):
    parent = {"database_id": notion_helper.day_database_id, "type": "database_id"}
    properties = {
        "标题": get_title(
            format_date(
                datetime.utcfromtimestamp(timestamp) + timedelta(hours=8),
                "%Y年%m月%d日",
            )
        ),
        "日期": get_date(
            start=format_date(datetime.utcfromtimestamp(timestamp) + timedelta(hours=8))
        ),
        "时长": get_number(duration),
        "时间戳": get_number(timestamp),
        "年": get_relation(
            [
                notion_helper.get_year_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
        "月": get_relation(
            [
                notion_helper.get_month_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
        "周": get_relation(
            [
                notion_helper.get_week_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
    }
    print(f"Inserting/updating Notion page with properties: {properties}")
    if page_id != None:
            notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        notion_helper.client.pages.create(
            parent=parent,
            icon=get_icon("https://www.notion.so/icons/target_red.svg"),
            properties=properties,
        )

def get_file():
    folder_path = "./OUT_FOLDER"
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        entries = os.listdir(folder_path)
        file_name = entries[0] if entries else None
        print(f"File found: {file_name}")
        return file_name
    else:
        print("OUT_FOLDER does not exist.")
        return None

HEATMAP_GUIDE = "https://mp.weixin.qq.com/s?__biz=MzI1OTcxOTI4NA==&mid=2247484145&idx=1&sn=81752852420b9153fc292b7873217651&chksm=ea75ebeadd0262fc65df100370d3f983ba2e52e2fcde2deb1ed49343fbb10645a77570656728&token=157143379&lang=zh_CN#rd"

if __name__ == "__main__":
    notion_helper = NotionHelper()
    weread_api = WeReadApi()

    # 处理热力图
    image_file = get_file()
    if image_file:
        image_url = f"https://raw.githubusercontent.com/{os.getenv('REPOSITORY')}/{os.getenv('REF').split('/')[-1]}/OUT_FOLDER/{image_file}"
        heatmap_url = f"https://heatmap.malinkang.com/?image={image_url}"
        print(f"Generated heatmap URL: {heatmap_url}")
        if notion_helper.heatmap_block_id:
            print(f"Updating heatmap block ID: {notion_helper.heatmap_block_id}")
            response = notion_helper.update_heatmap(
                block_id=notion_helper.heatmap_block_id, url=heatmap_url
            )
            print(f"Heatmap update response: {response}")
        else:
            print(f"更新热力图失败，没有添加热力图占位。具体参考：{HEATMAP_GUIDE}")
    else:
        print(f"更新热力图失败，没有生成热力图。具体参考：{HEATMAP_GUIDE}")

    # 获取书架数据
    bookshelf_books = weread_api.get_bookshelf()
    print(f"Bookshelf data: {json.dumps(bookshelf_books, indent=4)}")
    ll_bookshelf = next((shelf for shelf in bookshelf_books.get("archive", []) if shelf.get("name") == "ll的书架"), None)
    if ll_bookshelf:
        ll_bookshelf_books = set(ll_bookshelf.get("bookIds", []))
        print(f"ll的书架书籍 ID: {ll_bookshelf_books}")
    else:
        ll_bookshelf_books = set()
        print("没有找到名为 'll的书架' 的书架")

    # 获取阅读时长数据
    api_data = weread_api.get_api_data()
    # print(f"Full API data: {json.dumps(api_data, indent=4)}")

    # 正确地构建 readTimes，使其按书籍 ID 和时间戳存储
    readTimes = {}
    for book_id, time_data in api_data.get("readTimes", {}).items():
        if book_id in ll_bookshelf_books:
            for timestamp, duration in time_data.items():
                if book_id not in readTimes:
                    readTimes[book_id] = {}
                readTimes[book_id][int(timestamp)] = duration

    now = pendulum.now("Asia/Shanghai").start_of("day")
    today_timestamp = now.int_timestamp

    for book_id in ll_bookshelf_books:
        if book_id not in readTimes:
            readTimes[book_id] = {}
        if today_timestamp not in readTimes[book_id]:
            readTimes[book_id][today_timestamp] = 0

    print(f"Filtered and sorted reading times: {readTimes}")

    # 获取 Notion 中现有记录
    results = notion_helper.query_all(database_id=notion_helper.day_database_id)

    # 先收集所有现有时间戳的记录
    existing_timestamps = {result.get("properties").get("时间戳").get("number") for result in results}

    # 先更新已有记录
    for result in results:
        timestamp = result.get("properties").get("时间戳").get("number")
        duration = result.get("properties").get("时长").get("number")
        id = result.get("id")
        book_relation = result.get("properties").get("书籍")
        if book_relation and book_relation.get("relation"):
            book_id = book_relation.get("relation", [{}])[0].get("id")
        else:
            book_id = None

        if book_id in ll_bookshelf_books and book_id in readTimes:  # 只更新 ll 的书架中的书籍
            print(f"Processing Notion page ID: {id}, Timestamp: {timestamp}, Duration: {duration}")
            if timestamp in readTimes[book_id]:
                value = readTimes[book_id].pop(timestamp)
                if value != duration:
                    print(f"Updating Notion page ID: {id} with new duration: {value}")
                    insert_to_notion(page_id=id, timestamp=timestamp, duration=value)

    # 插入新记录
    for book_id, times in readTimes.items():
        for timestamp, value in times.items():
            if timestamp not in existing_timestamps:
                print(f"Inserting new Notion page with book_id: {book_id}, timestamp: {timestamp}, Duration: {value}")
                insert_to_notion(None, int(timestamp), value)
