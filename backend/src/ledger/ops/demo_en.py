"""英文演示家庭：把家庭的初始分类改为英文，并把演示账目的分类与备注换成英文（合成数据）。

由 demo_data --lang en 调用。分类改名走网站同一个 update_category（校验、版本、审计、幂等）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from ledger.ops.demo_data import DemoEntry

# 初始分类（seed/categories.json）的英文名；同名分类（如两处“待分类”）译名相同
CATEGORY_EN: dict[str, str] = {
    "食品与餐饮": "Food & dining",
    "超市综合购物": "Groceries",
    "超市食品": "Specialty food",
    "外出用餐": "Eating out",
    "学校餐食": "School lunches",
    "居家生活": "Home",
    "日用杂物": "Household supplies",
    "家具": "Furniture",
    "家电与耗材": "Appliances",
    "维修": "Repairs",
    "交通出行": "Transport",
    "公交": "Public transport",
    "校巴": "School bus",
    "打车": "Taxi & rideshare",
    "燃油": "Fuel",
    "停车与路桥": "Parking & tolls",
    "驾驶培训": "Driving lessons",
    "车辆保险": "Car insurance",
    "道路救援会员": "Roadside assistance",
    "通信与订阅": "Phone & subscriptions",
    "手机与话费": "Mobile",
    "软件订阅": "Subscriptions",
    "域名与网络服务": "Domains & web services",
    "医疗与个护": "Health & personal care",
    "药品": "Medicine",
    "个人护理": "Personal care",
    "药妆综合购物": "Pharmacy shopping",
    "教育与职业": "Education & work",
    "学习用品": "School supplies",
    "职业组织会费": "Professional fees",
    "娱乐休闲": "Leisure",
    "电影": "Movies",
    "其他娱乐": "Other leisure",
    "人情与往来": "Gifts & giving",
    "给他人转账": "Money to others",
    "礼金与礼物": "Gifts",
    "捐赠": "Donations",
    "其他支出": "Other spending",
    "邮寄": "Postage",
    "待分类": "Uncategorised",
    "工作收入": "Work income",
    "工资": "Salary",
    "奖金": "Bonus",
    "其他工作收入": "Other work income",
    "他人赠予": "Gifts received",
    "亲友赠予": "From family & friends",
    "其他赠予": "Other gifts",
    "其他收入": "Other income",
    "内部转账": "Transfers",
    "账户间转账": "Between accounts",
    "现金存取": "Cash in & out",
    "其他内部转账": "Other transfers",
    "换汇": "Currency exchange",
    "货币兑换": "Currency exchange",
    "信用卡还款": "Card repayments",
    "往来款": "IOUs",
    "借出": "Lent",
    "收回借款": "Loan repaid to me",
    "借入": "Borrowed",
    "归还借款": "Loan repaid by me",
    "代垫": "Paid for someone",
    "收回代垫": "Paid back to me",
    "其他往来": "Other IOUs",
}
# 叶子与一级同名时，叶子用单数（“信用卡还款／信用卡还款”）
LEAF_EN: dict[tuple[str, str], str] = {("信用卡还款", "信用卡还款"): "Card repayment"}

NOTE_EN: dict[str, str] = {
    "亚洲超市": "Asian supermarket",
    "Tai Ping 超市": "Tai Ping Supermarket",
    "菜市场": "Fruit & veg market",
    "咖啡": "Coffee",
    "早餐咖啡": "Morning coffee",
    "周末家庭晚餐": "Family dinner out",
    "火锅": "Hotpot",
    "日料": "Sushi",
    "披萨外卖": "Pizza takeaway",
    "越南粉": "Pho",
    "早午餐": "Brunch",
    "学校午餐卡充值": "School lunch top-up",
    "市区停车": "City parking",
    "HOP 卡充值": "HOP card top-up",
    "药房": "Pharmacy",
    "游泳馆": "Swimming pool",
    "博物馆": "Museum",
    "动物园": "Zoo",
    "保龄球": "Bowling",
    "农夫市集": "Farmers’ market",
    "车险月付": "Car insurance (monthly)",
    "每月转入储蓄账户": "Monthly transfer to savings",
    "手机月费（One NZ）": "Mobile plan (One NZ)",
    "手机月费（2degrees）": "Mobile plan (2degrees)",
    "iCloud 储存": "iCloud storage",
    "国内手机号月租": "China mobile number plan",
    "给父母的生活费": "Allowance for parents",
    "Spotify 家庭版": "Spotify Family",
    "工资（双周）": "Salary (fortnightly)",
    "兼职工资（双周）": "Part-time pay (fortnightly)",
    "AA 年费": "AA membership (annual)",
    "第三学期校巴月票": "Term 3 school bus pass",
    "Warehouse Stationery 开学文具": "Warehouse Stationery school supplies",
    "客厅沙发": "Living room sofa",
    "Event Cinemas 两张电影票": "Event Cinemas, 2 tickets",
    "替朋友垫付聚餐": "Covered a friend’s dinner",
    "朋友还聚餐垫付": "Friend paid back dinner",
    "水管维修": "Plumber – leaking pipe",
    "Briscoes 电水壶": "Briscoes kettle",
    "季度奖金": "Quarterly bonus",
    "同学生日礼物": "Birthday present for a classmate",
    "纽币换人民币（Wise）": "NZD to CNY (Wise)",
    "长辈给孩子的红包": "Red envelope from the grandparents",
    "NZ Post 寄信": "NZ Post letter",
    "Trade Me 卖二手童车": "Sold the old pram on Trade Me",
    "学校募捐": "School fundraiser",
    "学校假期活动营": "School holiday programme",
    "药妆店补货": "Pharmacy restock",
    "沙发价保差价退款": "Sofa price-match refund",
    "电水壶退货": "Kettle returned",
}
_NOTE_PATTERNS = [
    (re.compile(r"^(.+) 采购$"), "{} groceries"),
    (re.compile(r"^还清 (\d{4}-\d{2}) 信用卡账单$"), "Credit card statement {} paid in full"),
]
CJK = re.compile(r"[一-鿿]")


def _note(text: str) -> str:
    if text in NOTE_EN:
        return NOTE_EN[text]
    for rx, en in _NOTE_PATTERNS:
        m = rx.match(text)
        if m:
            return en.format(*m.groups())
    return text  # 已是英文（如品牌名 Z Energy、Uber）


def _cat(parent: str, leaf: str) -> tuple[str, str]:
    if not parent:
        return parent, leaf  # 退款沿用原支出分类
    return CATEGORY_EN.get(parent, parent), LEAF_EN.get((parent, leaf), CATEGORY_EN.get(leaf, leaf))


def localize(entries: list[DemoEntry]) -> list[DemoEntry]:
    """把演示账目的分类与备注换成英文。"""
    out = []
    for e in entries:
        parent, leaf = _cat(e.parent, e.leaf)
        out.append(replace(e, parent=parent, leaf=leaf, note=_note(e.note)))
    return out


def rename_categories(ctx: Any, actor: Any, fid: str) -> int:
    """把家庭目录中仍为中文初始名的分类改为英文；已改过的跳过（可重复执行）。返回改名数。"""
    from ledger.application import categories, repo

    cat = repo.catalog(ctx, fid)
    names = {c.category_id: c.name for c in cat.by_id.values()}
    tag = hashlib.sha256(fid.encode()).hexdigest()[:8]
    n = 0
    for c in sorted(cat.by_id.values(), key=lambda c: (c.parent_id is not None, c.category_id)):
        parent_zh = names.get(c.parent_id or "", "")
        en = LEAF_EN.get((parent_zh, c.name)) if c.parent_id else None
        en = en or CATEGORY_EN.get(c.name)
        if not en or en == c.name:
            continue
        categories.update_category(
            ctx,
            actor,
            fid,
            c.category_id,
            f"demo-en-{tag}-{c.category_id}",
            expected_version=c.version,
            name=en,
        )
        n += 1
    return n
