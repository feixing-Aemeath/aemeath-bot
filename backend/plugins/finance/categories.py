"""Deterministic local categories; remembered counterpart rules take priority."""

RULES = {
    "会员订阅": (
        "会员",
        "自动续费",
        "订阅",
        "apple.com",
        "app store",
        "icloud",
        "google*",
        "claude",
        "chatgpt",
        "openai",
        "deepseek",
        "网盘",
    ),
    "吃饭": (
        "食堂",
        "餐厅",
        "餐饮",
        "饭",
        "外卖",
        "咖啡",
        "coffee",
        "瑞幸",
        "星巴克",
        "茶姬",
        "蜜雪",
        "奶茶",
        "便利店",
        "盒马",
        "麦当劳",
        "肯德基",
        "老乡鸡",
        "鸡腿",
        "火锅",
        "烧烤",
        "店-桌",
        "饿了么",
    ),
    "交通": (
        "滴滴",
        "地铁",
        "公交",
        "12306",
        "哈啰",
        "轨道交通",
        "天府通",
        "交通卡",
        "出行",
        "携程",
        "单程",
        "火车",
        "机票",
    ),
    "购物": ("淘宝", "天猫", "京东", "拼多多", "抖音", "旗舰店", "百货", "苹果官网"),
    "娱乐": ("电影", "游戏", "影院", "游乐", "steam", "健身"),
    "学习": ("书店", "打印", "文具", "大学", "学院"),
    "通讯": ("话费", "流量", "中国移动", "中国联通", "中国电信"),
    "医疗": ("医院", "药房", "挂号", "医疗"),
    "其他": (),
}
# Platforms that sell everything: they decide only when the item itself says nothing (a gym
# session bought on 美团 is not a meal).
PLATFORMS = {"美团": "吃饭"}
CATEGORIES = tuple(RULES)
# Renewals charge on their own day, wherever the user is: never spending at home.
AWAY = frozenset({"会员订阅"})
# A meal from these is delivered: what 记账 suggests ordering less of when money is short.
TAKEOUT = ("外卖", "饿了么", "美团")


def takeout(row):
    text = (row["counterpart"] + " " + row["item"]).casefold()
    return row["category"] == "吃饭" and any(word in text for word in TAKEOUT)


def categorize(counterpart, item, remembered, configured):
    if counterpart in remembered:
        return remembered[counterpart]
    text = (counterpart + " " + item).casefold()
    for keyword, category in configured.items():
        if keyword.casefold() in text:
            return category
    # What was bought before where: the item first, then the merchant's name.
    for words in (item.casefold(), text):
        for category, keywords in RULES.items():
            if any(word in words for word in keywords):
                return category
    for platform, category in PLATFORMS.items():
        if platform in text:
            return category
    return "其他"
