"""Display names for the workout types already stored by the health plugin."""

NAMES = {
    "Running": "跑步",
    "Walking": "步行",
    "Cycling": "骑行",
    "Swimming": "游泳",
    "TraditionalStrengthTraining": "力量训练",
    "FunctionalStrengthTraining": "功能性力量训练",
    "Yoga": "瑜伽",
    "Pilates": "普拉提",
    "Hiking": "徒步",
    "Dance": "舞蹈",
    "Elliptical": "椭圆机",
    "Rowing": "划船",
    "CoreTraining": "核心训练",
}


def exercise_name(activity):
    activity = activity.removeprefix("HKWorkoutActivityType")
    return next(
        (name for kind, name in NAMES.items() if kind.casefold() == activity.casefold()), "运动"
    )
