"""第二课堂 (dekt.example.edu.cn): activities open for sign-up and the user's own sign-ups.

The user agreed on 2026-11-14 that the backend may use the site's data endpoints. It does so
only from inside the user's logged-in Chrome tab: Chrome runs the fixed script below in the tab
showing the site's student page (dekt.example.edu.cn/yb/, the 易班 page the school's 应用中心
opens), which makes the same read-only requests the page makes itself and returns plain JSON.
The backend never logs in, never sees the login cookie and never signs up or cancels.

Chrome runs scripts from AppleScript only with 「允许 Apple 事件中的 JavaScript」 switched on
(查看 → 开发者), which is the user's to switch.
"""

import json
import re
from datetime import UTC, datetime

from core import helper_process

TAB_URL = "dekt.example.edu.cn/yb/"
SITE = "dekt"  # source_refs of records read from the site: "dekt:<activity id>"

# Runs `item 2` in the first Chrome tab whose URL contains `item 1`; never starts Chrome or
# brings it forward. Errors come back as JSON with a fixed code.
RUN = """on run argv
    set wanted to item 1 of argv
    set script_text to item 2 of argv
    if application "Google Chrome" is not running then return "{\\"error\\":\\"chrome_not_running\\"}"
    tell application "Google Chrome"
        repeat with w in windows
            repeat with t in tabs of w
                if (URL of t) contains wanted then
                    try
                        return execute t javascript script_text
                    on error number n
                        if n is 12 then return "{\\"error\\":\\"chrome_js_disabled\\"}"
                        return "{\\"error\\":\\"chrome_js_failed\\"}"
                    end try
                end if
            end repeat
        end repeat
    end tell
    return "{\\"error\\":\\"dekt_tab_not_open\\"}"
end run"""

# The page keeps the logged-in user's ID in localStorage and sends it with every request.
# Synchronous requests, because AppleScript returns the script's value without waiting.
# Endpoints and parameters as the page's own code uses them (2026-11-14): the activity list
# with type 2 is the 「报名中」 tab, 100 to a page; MyActivity is 「我的活动」 (isTeam 0
# personal, 1 team sign-ups); getActivity is an activity's page, whose `canEnroll` is what its
# sign-up button says to this user (empty: they may sign up) and whose `credit` lists the
# credits of each award.
SCRIPT = r"""(() => {
  const uid = localStorage.getItem('userId');
  if (!uid) return JSON.stringify({error: 'dekt_logged_out'});
  const ask = (method, path, params) => {
    const query = new URLSearchParams(params).toString();
    const request = new XMLHttpRequest();
    if (method === 'GET') {
      request.open('GET', path + '?' + query, false);
      request.send();
    } else {
      request.open('POST', path, false);
      request.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded;charset=UTF-8');
      request.send(query);
    }
    let body;
    try { body = JSON.parse(request.responseText); } catch (e) { throw 'dekt_logged_out'; }
    if (request.status !== 200 || body.code !== 200 || !body.data) throw 'dekt_request_failed';
    return body.data;
  };
  const pages = (method, path, params) => {
    let all = [];
    for (let page = 0; page < 5; page++) {
      const items = ask(method, path, {...params, pageIndex: page, userId: uid});
      if (!Array.isArray(items)) throw 'dekt_request_failed';
      all = all.concat(items);
      if (items.length < 100) break;
    }
    return all;
  };
  const activity = (a) => ({
    id: String(a.acid || a.hdbh || ''), name: a.name, start: a.startdatetime, end: a.enddatetime,
    signup_end: a.enrolenddatetime || null, place: a.location, organizer: a.zzmc, module: a.ekey,
  });
  // Whether this user may sign up, and the credits for taking part; unknown when the page fails.
  const details = (a) => {
    try {
      const page = ask('GET', '/wxController/getActivity', {acid: a.id, openId: uid});
      const credits = (page.credit || []).map((c) => [String(c.description || ''), parseFloat(c.xf)]);
      const taking_part = credits.find(([award]) => award.includes('参与'));
      const least = credits.map(([, xf]) => xf).filter((xf) => xf > 0);
      return {...a, can_enroll: typeof page.canEnroll === 'string' ? page.canEnroll : null,
        credits: taking_part ? taking_part[1] : (least.length ? Math.min(...least) : null)};
    } catch (e) {
      return {...a, can_enroll: null, credits: null};
    }
  };
  try {
    const open = pages('GET', '/wxController/ActivityForStu', {edt: '', hdxs: 10, type: 2});
    const mine = pages('POST', '/acinfoService/MyActivity', {isTeam: 0})
      .concat(pages('POST', '/acinfoService/MyActivity', {isTeam: 1}));
    return JSON.stringify({open: open.map(activity).map(details), mine: mine.map(activity)});
  } catch (e) {
    return JSON.stringify({error: typeof e === 'string' ? e : 'dekt_request_failed'});
  }
})()"""

# 「模块 类型」 as the site labels an activity (ekey), e.g. 「美育润心 社团活动」.
KINDS = {
    "竞赛展演": "比赛",
    "竞赛评比": "比赛",
    "学习培训": "讲座",
    "实践服务": "志愿",
    "运动实践": "文体",
}
ARTS = ("美育润心", "体育健身")
# What an activity's sign-up button says to this user (canEnroll), as a fixed reason they cannot
# sign up; empty, 「报名通过」 and 「您已报名该活动」 (signed up already) mean they can or did.
NOT_FOR_YOU = "不在报名范围"
FULL = "名额已满"
BLOCKS = {"报名对象不符": NOT_FOR_YOU, "名额不足": FULL}
OPEN = ("", "报名通过", "您已报名该活动")


async def fetch(timeout=60):
    """{"open": [...], "mine": [...]} from the logged-in tab; raises RuntimeError(code)."""
    try:
        code, output = await helper_process.run(
            ["/usr/bin/osascript", "-e", RUN, TAB_URL, SCRIPT], None, timeout, capture_error=True
        )
    except TimeoutError:
        # The first time, macOS holds the call until the user answers whether the backend may
        # control Chrome.
        raise RuntimeError("chrome_timeout") from None
    if code:
        # -1743: the backend may not control Chrome yet (系统设置 → 隐私与安全性 → 自动化).
        raise RuntimeError("chrome_automation_denied" if b"-1743" in output else "chrome_failed")
    return parse(output)


def parse(output):
    try:
        data = json.loads(output)
    except ValueError:
        raise RuntimeError("dekt_bad_reply") from None
    if not isinstance(data, dict):
        raise RuntimeError("dekt_bad_reply")
    error = data.get("error")
    if error:
        # Fixed codes only, never text from the page.
        raise RuntimeError(
            error if re.fullmatch(r"[a-z][a-z0-9_]{2,40}", str(error)) else "dekt_bad_reply"
        )
    if not isinstance(data.get("open"), list) or not isinstance(data.get("mine"), list):
        raise RuntimeError("dekt_bad_reply")
    return data


def site_time(value, zone):
    """The site's local 「2026-11-15 00:00:00」 as a UTC ISO string; None when missing."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip()[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return parsed.replace(tzinfo=zone).astimezone(UTC).isoformat()


def clip(value, size):
    return value.strip()[:size] if isinstance(value, str) and value.strip() else None


def event_values(item, zone):
    """campus_events fields for one activity as the site lists it; None without a name."""
    title = clip(item.get("name"), 80)
    if not title:
        return None
    module, _, kind_label = (item.get("module") or "").strip().partition(" ")
    kind_label = kind_label.strip()
    kind = KINDS.get(kind_label) or (
        "文体" if kind_label == "社团活动" and module in ARTS else "其他"
    )
    organizer = clip(item.get("organizer"), 40)
    values = {
        "title": title,
        "kind": kind,
        "starts_at": site_time(item.get("start"), zone),
        "ends_at": site_time(item.get("end"), zone),
        "has_time": 1,
        "place": clip(item.get("place"), 80),
        "signup_deadline": site_time(item.get("signup_end"), zone),
        "note": f"{organizer}主办" if organizer else None,
        "credit_kind": clip(module, 20),
    }
    if "can_enroll" in item:  # open activities: their own page was read
        values["signup_block"] = block(item["can_enroll"])
        credits = item.get("credits")
        if isinstance(credits, int | float) and not isinstance(credits, bool) and 0 < credits <= 10:
            values["credits"] = float(credits)
    return values


def block(can_enroll):
    """Why the user cannot sign up, in fixed words; None when they can or it is unknown."""
    if not isinstance(can_enroll, str) or can_enroll.strip() in OPEN:
        return None
    return BLOCKS.get(can_enroll.strip(), "暂不能报名")


def ref(item):
    identifier = str(item.get("id") or "").strip()
    return f"{SITE}:{identifier}" if identifier else f"{SITE}:{item.get('name', '')[:40]}"
