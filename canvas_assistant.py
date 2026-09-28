from playwright.sync_api import Error as PlaywrightError, sync_playwright
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse
from html import unescape
from html.parser import HTMLParser
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


BASE_URL = "https://canvas.northwestern.edu"
PROFILE_DIR = Path(__file__).parent / "browser_profile"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
REFRESH_HOURS = 5
LOGIN_WAIT_SECONDS = 600
CREDENTIAL_TARGET = "CanvasLocalAssistant/NorthwesternSSO"
LANGUAGE = os.environ.get("CANVAS_ASSISTANT_LANGUAGE", "zh").lower()


def t(zh, en):
    return en if LANGUAGE.startswith("en") else zh


DUE_LABELS = {
    "overdue": t("已逾期（未提交）", "Overdue (not submitted)"),
    "due_24h": t("24 小时内截止", "Due within 24 hours"),
    "due_3d": t("3 天内截止", "Due within 3 days"),
    "due_7d": t("7 天内截止", "Due within 7 days"),
    "later": t("稍后截止", "Due later"),
    "no_due": t("没有截止时间", "No due date"),
    "completed": t("已提交/已完成", "Submitted / completed"),
}
DUE_ORDER = tuple(DUE_LABELS)


class LoginRequired(Exception):
    pass


if sys.platform == "win32":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    _ADVAPI32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    _ADVAPI32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _ADVAPI32.CredWriteW.restype = wintypes.BOOL
    _ADVAPI32.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    _ADVAPI32.CredReadW.restype = wintypes.BOOL
    _ADVAPI32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _ADVAPI32.CredDeleteW.restype = wintypes.BOOL
    _ADVAPI32.CredFree.argtypes = [ctypes.c_void_p]
    _ADVAPI32.CredFree.restype = None


def save_windows_credential(username, password):
    """Store the SSO password in Windows Credential Manager or macOS Keychain."""
    if sys.platform == "darwin":
        subprocess.run(
            ["security", "add-generic-password", "-a", "CanvasLocalAssistant", "-s", CREDENTIAL_TARGET + "/username", "-w", username, "-U"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["security", "add-generic-password", "-a", "CanvasLocalAssistant", "-s", CREDENTIAL_TARGET + "/password", "-w", password, "-U"],
            check=True, capture_output=True, text=True,
        )
        return
    if sys.platform != "win32":
        raise OSError(t("此系统暂不支持安全保存登录信息", "Secure credential storage is not supported on this system"))
    encoded = password.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _CREDENTIALW()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = CREDENTIAL_TARGET
    credential.UserName = username
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    if not _ADVAPI32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def load_windows_credential():
    if sys.platform == "darwin":
        try:
            username = subprocess.run(
                ["security", "find-generic-password", "-a", "CanvasLocalAssistant", "-s", CREDENTIAL_TARGET + "/username", "-w"],
                check=True, capture_output=True, text=True,
            ).stdout.rstrip("\n")
            password = subprocess.run(
                ["security", "find-generic-password", "-a", "CanvasLocalAssistant", "-s", CREDENTIAL_TARGET + "/password", "-w"],
                check=True, capture_output=True, text=True,
            ).stdout.rstrip("\n")
            return username, password
        except subprocess.CalledProcessError:
            return None
    if sys.platform != "win32":
        return None
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not _ADVAPI32.CredReadW(
        CREDENTIAL_TARGET, 1, 0, ctypes.byref(pointer)
    ):
        return None
    try:
        credential = pointer.contents
        password_bytes = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        return credential.UserName or "", password_bytes.decode("utf-16-le")
    finally:
        _ADVAPI32.CredFree(pointer)


def delete_windows_credential():
    if sys.platform == "darwin":
        for suffix in ("/username", "/password"):
            subprocess.run(
                ["security", "delete-generic-password", "-a", "CanvasLocalAssistant", "-s", CREDENTIAL_TARGET + suffix],
                check=False, capture_output=True, text=True,
            )
        return
    if sys.platform != "win32":
        return
    result = _ADVAPI32.CredDeleteW(CREDENTIAL_TARGET, 1, 0)
    if not result and ctypes.get_last_error() not in (0, 1168):
        raise ctypes.WinError()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value):
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        return clean_text(unescape(" ".join(parser.parts)))
    except Exception:
        return clean_text(value)


def parse_canvas_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def due_category(due_at, completed, now=None):
    if completed:
        return "completed"
    due = parse_canvas_time(due_at)
    if not due:
        return "no_due"
    now = now or datetime.now().astimezone()
    delta = due.astimezone() - now.astimezone()
    if delta.total_seconds() < 0:
        return "overdue"
    if delta <= timedelta(hours=24):
        return "due_24h"
    if delta <= timedelta(days=3):
        return "due_3d"
    if delta <= timedelta(days=7):
        return "due_7d"
    return "later"


def previous_announcement_ids():
    path = DATA_DIR / "latest.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        announcement.get("id")
        for course in old.get("courses", [])
        for announcement in course.get("announcements", [])
        if announcement.get("id") is not None
    }


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _next_link(link_header):
    for part in (link_header or "").split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def api_pages(context, path, params=None):
    """Read every page of a Canvas API collection using browser login cookies."""
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params, doseq=True)
    result = []
    while url:
        response = context.request.get(url, timeout=60000)
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status in (401, 403) or "application/json" not in content_type:
            raise LoginRequired("Canvas login is required")
        if not response.ok:
            raise RuntimeError(f"Canvas API returned HTTP {response.status}: {url}")
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected Canvas API response: {path}")
        result.extend(payload)
        url = _next_link(response.headers.get("link"))
    return result


def wait_for_login(context, page, status_callback):
    status_callback(t("需要登录：请在打开的浏览器中完成 Northwestern SSO…", "Sign-in required: complete Northwestern SSO in the browser…"))
    try:
        page.goto(BASE_URL, wait_until="commit", timeout=60000)
    except PlaywrightError as exc:
        # Northwestern immediately redirects to Microsoft SSO. Playwright may
        # report the first navigation as interrupted even though login is fine.
        if "interrupted by another navigation" not in str(exc).lower():
            raise
    deadline = datetime.now() + timedelta(seconds=LOGIN_WAIT_SECONDS)
    saved_credential = load_windows_credential()
    while datetime.now() < deadline:
        # Microsoft shows this after SSO/Duo. Confirming it lets its persistent
        # cookie be stored in browser_profile, so reopening the app normally
        # does not require another interactive login.
        current_url = page.url
        current_host = (urlparse(current_url).hostname or "").lower()
        if current_host == "login.microsoftonline.com":
            try:
                if saved_credential:
                    username, password = saved_credential
                    email_input = page.locator('input[type="email"]:visible')
                    password_input = page.locator('input[type="password"]:visible')
                    if email_input.count():
                        status_callback(t("正在使用安全凭据填写账户…", "Filling your account from secure credential storage…"))
                        email_input.first.fill(username, timeout=3000)
                        page.locator("#idSIButton9").click(timeout=3000)
                        page.wait_for_timeout(1200)
                    elif password_input.count():
                        status_callback(t("正在安全填写 Microsoft SSO 密码…", "Securely filling your Microsoft SSO password…"))
                        password_input.first.fill(password, timeout=3000)
                        page.locator("#idSIButton9").click(timeout=3000)
                        page.wait_for_timeout(1200)

                stay_signed_in = page.locator("#idSIButton9")
                prompt = page.locator("#KmsiDescription, #kmsiTitle, text=保持登录状态")
                if stay_signed_in.count() and prompt.count():
                    checkbox = page.locator("#KmsiCheckboxField")
                    if checkbox.count() and not checkbox.is_checked():
                        checkbox.check(timeout=2000)
                    status_callback(t("正在保存 Microsoft 登录状态…", "Saving your Microsoft sign-in session…"))
                    stay_signed_in.click(timeout=3000)
                    page.wait_for_timeout(1500)
            except PlaywrightError:
                # The page may navigate while the locators are being checked.
                pass
        try:
            api_pages(context, "/api/v1/users/self/courses", {"per_page": 1})
            # Give Chromium a moment to commit Microsoft/Canvas cookies before
            # the persistent context is closed after this refresh.
            page.wait_for_timeout(1000)
            return
        except LoginRequired:
            page.wait_for_timeout(1500)
    raise TimeoutError(t("等待登录超时（10 分钟）", "Sign-in timed out after 10 minutes"))


def _open_context(playwright, headless):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1350, "height": 900},
    )


def read_canvas(status_callback=lambda _message: None):
    """Fetch all enrolled courses and assignments, opening SSO only if needed."""
    with sync_playwright() as playwright:
        context = _open_context(playwright, headless=True)
        try:
            course_params = {
                "per_page": 100,
                "include[]": ["term"],
                "state[]": ["available", "completed"],
            }
            try:
                courses_raw = api_pages(context, "/api/v1/courses", course_params)
            except LoginRequired:
                context.close()
                context = _open_context(playwright, headless=False)
                page = context.pages[0] if context.pages else context.new_page()
                wait_for_login(context, page, status_callback)
                courses_raw = api_pages(context, "/api/v1/courses", course_params)

            courses = []
            known_announcement_ids = previous_announcement_ids()
            now = datetime.now().astimezone()
            for index, raw_course in enumerate(courses_raw, start=1):
                course_id = raw_course.get("id")
                if not course_id or raw_course.get("access_restricted_by_date"):
                    continue
                name = clean_text(raw_course.get("name") or raw_course.get("course_code"))
                status_callback(t(f"正在读取课程 {index}/{len(courses_raw)}：{name}", f"Reading course {index}/{len(courses_raw)}: {name}"))
                error = ""
                try:
                    assignments_raw = api_pages(
                        context,
                        f"/api/v1/courses/{course_id}/assignments",
                        {"per_page": 100, "order_by": "due_at", "include[]": ["submission"]},
                    )
                except Exception as exc:
                    assignments_raw = []
                    error = str(exc)

                assignments = []
                seen_ids = set()
                for assignment in assignments_raw:
                    assignment_id = assignment.get("id")
                    if assignment_id in seen_ids:
                        continue
                    seen_ids.add(assignment_id)
                    submission = assignment.get("submission") or {}
                    submission_state = submission.get("workflow_state") or ""
                    completed = submission_state in {"submitted", "graded", "pending_review"}
                    assignments.append({
                        "id": assignment_id,
                        "title": clean_text(assignment.get("name")),
                        "due_at": assignment.get("due_at"),
                        "unlock_at": assignment.get("unlock_at"),
                        "lock_at": assignment.get("lock_at"),
                        "points_possible": assignment.get("points_possible"),
                        "published": assignment.get("published"),
                        "submission_state": submission_state,
                        "submitted_at": submission.get("submitted_at"),
                        "completed": completed,
                        "due_category": due_category(assignment.get("due_at"), completed, now),
                        "url": assignment.get("html_url") or "",
                    })

                announcement_error = ""
                try:
                    announcements_raw = api_pages(
                        context,
                        "/api/v1/announcements",
                        {
                            "per_page": 50,
                            "context_codes[]": [f"course_{course_id}"],
                            "start_date": (now - timedelta(days=30)).isoformat(),
                            "end_date": now.isoformat(),
                        },
                    )
                except Exception as exc:
                    announcements_raw = []
                    announcement_error = str(exc)

                announcements = []
                for announcement in announcements_raw:
                    announcement_id = announcement.get("id")
                    author = announcement.get("author") or {}
                    announcements.append({
                        "id": announcement_id,
                        "title": clean_text(announcement.get("title")),
                        "posted_at": announcement.get("posted_at") or announcement.get("created_at"),
                        "author": clean_text(author.get("display_name") or author.get("name")),
                        "message": html_to_text(announcement.get("message"))[:600],
                        "url": announcement.get("html_url") or "",
                        "is_new": announcement_id not in known_announcement_ids,
                    })

                courses.append({
                    "id": str(course_id),
                    "name": name,
                    "course_code": clean_text(raw_course.get("course_code")),
                    "term": clean_text((raw_course.get("term") or {}).get("name")),
                    "workflow_state": raw_course.get("workflow_state"),
                    "url": f"{BASE_URL}/courses/{course_id}",
                    "assignments": assignments,
                    "announcements": announcements,
                    "error": error,
                    "announcement_error": announcement_error,
                })

            return {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "courses": courses,
            }
        finally:
            context.close()


def write_output(result):
    json_path = DATA_DIR / "latest.json"
    txt_path = DATA_DIR / "latest.txt"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"Canvas snapshot: {result['generated_at']}", ""]
    for course in result["courses"]:
        lines.extend(["=" * 72, course["name"]])
        if course["term"]:
            lines.append(f"Term: {course['term']}")
        lines.extend([course["url"], ""])
        if course["error"]:
            lines.append(f"  ERROR: {course['error']}")
        elif not course["assignments"]:
            lines.append("  No assignments found.")
        else:
            for category in DUE_ORDER:
                matching = [a for a in course["assignments"] if a.get("due_category") == category]
                if not matching:
                    continue
                lines.append(f"  [{DUE_LABELS[category]}] ({len(matching)})")
                for assignment in matching:
                    lines.append(f"    - {assignment['title']}")
                    if assignment["due_at"]:
                        lines.append(f"      Due: {assignment['due_at']}")
                    if assignment["points_possible"] is not None:
                        lines.append(f"      Points: {assignment['points_possible']}")
                    if assignment["url"]:
                        lines.append(f"      {assignment['url']}")

        announcements = course.get("announcements", [])
        lines.append("")
        lines.append(f"  [{t('最近 30 天公告', 'Announcements from the last 30 days')}] ({len(announcements)})")
        if course.get("announcement_error"):
            lines.append(f"    ERROR: {course['announcement_error']}")
        elif not announcements:
            lines.append("    No recent announcements.")
        else:
            for announcement in announcements:
                new_mark = "NEW · " if announcement.get("is_new") else ""
                lines.append(f"    - {new_mark}{announcement['title']}")
                details = " · ".join(filter(None, [announcement.get("author"), announcement.get("posted_at")]))
                if details:
                    lines.append(f"      {details}")
                if announcement.get("message"):
                    lines.append(f"      {announcement['message']}")
                if announcement.get("url"):
                    lines.append(f"      {announcement['url']}")
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, txt_path


class CanvasAssistantApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Canvas Local Assistant")
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)
        self.events = queue.Queue()
        self.running = False
        self.next_refresh = None
        self.result = None
        self.item_urls = {}

        style = ttk.Style()
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("CardValue.TLabel", font=("Segoe UI", 20, "bold"), foreground="#4f2683")
        style.configure("CardTitle.TLabel", font=("Segoe UI", 9), foreground="#555555")
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        header = ttk.Frame(frame)
        header.pack(fill="x")
        header_left = ttk.Frame(header)
        header_left.pack(side="left", fill="x", expand=True)
        ttk.Label(header_left, text=t("Canvas 学习中心", "Canvas Study Center"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(header_left, text=t("全部课程、截止日期与教师公告 · 每 5 小时自动同步", "All courses, deadlines, and announcements · syncs every 5 hours")).pack(anchor="w", pady=(2, 0))

        buttons = ttk.Frame(header)
        buttons.pack(side="right")
        self.refresh_button = ttk.Button(buttons, text=t("↻ 立即刷新", "↻ Refresh now"), command=self.start_refresh)
        self.refresh_button.pack(side="left")
        ttk.Button(buttons, text=t("登录设置", "Sign-in settings"), command=self.login_settings).pack(side="left", padx=8)
        ttk.Button(buttons, text=t("数据目录", "Data folder"), command=self.open_data_directory).pack(side="left")

        self.status_var = tk.StringVar(value=t("准备启动…", "Ready to start…"))
        self.next_var = tk.StringVar(value=t("下次刷新：尚未安排", "Next refresh: not scheduled"))
        status_row = ttk.Frame(frame)
        status_row.pack(fill="x", pady=(12, 8))
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")
        ttk.Label(status_row, textvariable=self.next_var, foreground="#666666").pack(side="right")

        cards = ttk.Frame(frame)
        cards.pack(fill="x", pady=(0, 12))
        self.card_vars = {key: tk.StringVar(value="—") for key in ("courses", "overdue", "week", "news")}
        for column, (key, title) in enumerate((
            ("courses", t("课程", "Courses")), ("overdue", t("已逾期", "Overdue")),
            ("week", t("未来 7 天", "Next 7 days")), ("news", t("新公告", "New announcements"))
        )):
            card = ttk.LabelFrame(cards, padding=(18, 8))
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
            cards.columnconfigure(column, weight=1)
            ttk.Label(card, textvariable=self.card_vars[key], style="CardValue.TLabel").pack(anchor="w")
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")

        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        self.todo_tab = ttk.Frame(self.tabs, padding=10)
        self.news_tab = ttk.Frame(self.tabs, padding=10)
        self.course_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.todo_tab, text=t("  作业与截止日期  ", "  Assignments & deadlines  "))
        self.tabs.add(self.news_tab, text=t("  教师公告  ", "  Announcements  "))
        self.tabs.add(self.course_tab, text=t("  全部课程  ", "  All courses  "))

        filter_row = ttk.Frame(self.todo_tab)
        filter_row.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_row, text=t("显示：", "Show:" )).pack(side="left")
        self.assignment_filter = tk.StringVar(value=t("近期未完成", "Upcoming incomplete"))
        filter_box = ttk.Combobox(
            filter_row,
            textvariable=self.assignment_filter,
            state="readonly",
            width=18,
            values=[t("近期未完成", "Upcoming incomplete"), t("全部未完成", "All incomplete"),
                    t("已逾期", "Overdue"), t("24 小时内", "Within 24 hours"),
                    t("3 天内", "Within 3 days"), t("7 天内", "Within 7 days"),
                    t("稍后截止", "Due later"), t("无截止时间", "No due date"),
                    t("已完成", "Completed"), t("全部作业", "All assignments")],
        )
        filter_box.pack(side="left")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._render_assignments())
        ttk.Label(filter_row, text=t("双击任意一行可在浏览器中打开", "Double-click a row to open it in your browser"), foreground="#666666").pack(side="right")

        self.assignment_tree = self._make_tree(
            self.todo_tab,
            ("course", "category", "title", "due", "points"),
            (("course", t("课程", "Course"), 220), ("category", t("分类", "Category"), 120),
             ("title", t("作业", "Assignment"), 350), ("due", t("截止时间", "Due"), 175),
             ("points", t("分值", "Points"), 70)),
        )
        self.assignment_tree.tag_configure("overdue", foreground="#b42318")
        self.assignment_tree.tag_configure("urgent", foreground="#c45a00")

        news_pane = ttk.Panedwindow(self.news_tab, orient="vertical")
        news_pane.pack(fill="both", expand=True)
        news_top = ttk.Frame(news_pane)
        news_bottom = ttk.LabelFrame(news_pane, text=t("公告内容", "Announcement details"), padding=8)
        news_pane.add(news_top, weight=3)
        news_pane.add(news_bottom, weight=2)
        self.news_tree = self._make_tree(
            news_top,
            ("new", "course", "title", "author", "posted"),
            (("new", t("状态", "Status"), 60), ("course", t("课程", "Course"), 240),
             ("title", t("标题", "Title"), 350), ("author", t("发布人", "Author"), 130),
             ("posted", t("发布时间", "Posted"), 165)),
        )
        self.news_tree.tag_configure("new", foreground="#4f2683")
        self.news_tree.bind("<<TreeviewSelect>>", self._show_announcement_detail)
        self.news_detail = tk.Text(news_bottom, height=7, wrap="word", state="disabled", font=("Segoe UI", 10), relief="flat")
        self.news_detail.pack(fill="both", expand=True)

        self.course_tree = self._make_tree(
            self.course_tab,
            ("course", "term", "total", "pending", "done", "news"),
            (("course", t("课程", "Course"), 360), ("term", t("学期", "Term"), 170),
             ("total", t("作业", "Assignments"), 80), ("pending", t("未完成", "Incomplete"), 90),
             ("done", t("已完成", "Completed"), 90), ("news", t("近 30 天公告", "30-day announcements"), 110)),
        )

        self.root.after(300, self._poll_events)
        self.root.after(700, self._initial_start)

    def _make_tree(self, parent, columns, definitions):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        tree = ttk.Treeview(container, columns=columns, show="headings", selectmode="browse")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for key, title, width in definitions:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, stretch=True)
        tree.bind("<Double-1>", self._open_selected_item)
        return tree

    def _initial_start(self):
        if load_windows_credential() is None:
            self.login_settings(first_run=True)
        self.start_refresh()

    def login_settings(self, first_run=False):
        existing = load_windows_credential()
        dialog = tk.Toplevel(self.root)
        dialog.title(t("Northwestern 登录设置", "Northwestern sign-in settings"))
        dialog.geometry("480x300")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=t("保存 Microsoft SSO 登录", "Save Microsoft SSO sign-in"), font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text=t(
                ("密码由 macOS Keychain 安全保存" if sys.platform == "darwin" else "密码由 Windows 凭据管理器加密保存") + "，仅在微软官方登录域名自动填写。Duo 确认仍由你完成。",
                ("Your password is protected by macOS Keychain" if sys.platform == "darwin" else "Your password is protected by Windows Credential Manager") + ". It is filled only on Microsoft's official sign-in domain. You still approve Duo prompts.",
            ),
            wraplength=430,
        ).pack(anchor="w", pady=(4, 14))

        form = ttk.Frame(body)
        form.pack(fill="x")
        ttk.Label(form, text=t("账户邮箱", "Account email")).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Label(form, text=t("密码", "Password")).grid(row=1, column=0, sticky="w", pady=5)
        username_var = tk.StringVar(value=existing[0] if existing else "")
        password_var = tk.StringVar()
        username_entry = ttk.Entry(form, textvariable=username_var, width=42)
        password_entry = ttk.Entry(form, textvariable=password_var, width=42, show="●")
        username_entry.grid(row=0, column=1, padx=(12, 0), pady=5)
        password_entry.grid(row=1, column=1, padx=(12, 0), pady=5)
        if existing:
            ttk.Label(form, text=t("密码留空表示保留已保存的密码", "Leave password blank to keep the saved password"), foreground="#666666").grid(
                row=2, column=1, sticky="w"
            )

        button_row = ttk.Frame(body)
        button_row.pack(fill="x", pady=(18, 0))

        def save():
            username = username_var.get().strip()
            password = password_var.get()
            if not username or (not password and not existing):
                messagebox.showwarning(t("信息不完整", "Missing information"), t("请输入账户邮箱和密码。", "Enter your account email and password."), parent=dialog)
                return
            if not password and existing:
                password = existing[1]
            try:
                save_windows_credential(username, password)
            except OSError as exc:
                messagebox.showerror(t("保存失败", "Save failed"), str(exc), parent=dialog)
                return
            dialog.destroy()

        def remove():
            try:
                delete_windows_credential()
            except OSError as exc:
                messagebox.showerror(t("删除失败", "Delete failed"), str(exc), parent=dialog)
                return
            dialog.destroy()

        ttk.Button(button_row, text=t("保存", "Save"), command=save).pack(side="right")
        ttk.Button(button_row, text=t("取消", "Cancel"), command=dialog.destroy).pack(side="right", padx=8)
        if existing:
            ttk.Button(button_row, text=t("删除已保存登录", "Delete saved sign-in"), command=remove).pack(side="left")

        username_entry.focus_set()
        dialog.bind("<Return>", lambda _event: save())
        dialog.wait_window()

    def _post(self, kind, value):
        self.events.put((kind, value))

    def start_refresh(self):
        if self.running:
            return
        self.running = True
        self.refresh_button.configure(state="disabled")
        self.status_var.set(t("正在连接 Canvas…", "Connecting to Canvas…"))
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            result = read_canvas(lambda message: self._post("status", message))
            paths = write_output(result)
            self._post("success", (result, paths))
        except Exception as exc:
            self._post("error", str(exc))

    def _poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(value)
                elif kind == "success":
                    result, _paths = value
                    self.running = False
                    self.refresh_button.configure(state="normal")
                    self.next_refresh = datetime.now() + timedelta(hours=REFRESH_HOURS)
                    total = sum(len(course["assignments"]) for course in result["courses"])
                    new_announcements = sum(
                        1 for course in result["courses"]
                        for item in course.get("announcements", []) if item.get("is_new")
                    )
                    self.status_var.set(t(
                        f"刷新成功：{len(result['courses'])} 门课程，{total} 个作业，{new_announcements} 条新公告",
                        f"Refresh complete: {len(result['courses'])} courses, {total} assignments, {new_announcements} new announcements",
                    ))
                    self._show_summary(result)
                elif kind == "error":
                    self.running = False
                    self.refresh_button.configure(state="normal")
                    self.next_refresh = datetime.now() + timedelta(hours=REFRESH_HOURS)
                    self.status_var.set(t(f"刷新失败：{value}", f"Refresh failed: {value}"))
                    messagebox.showerror(t("Canvas 刷新失败", "Canvas refresh failed"), value)
        except queue.Empty:
            pass

        if self.next_refresh:
            remaining = self.next_refresh - datetime.now()
            if remaining.total_seconds() <= 0 and not self.running:
                self.start_refresh()
            else:
                seconds = max(0, int(remaining.total_seconds()))
                hours, remainder = divmod(seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                self.next_var.set(t(
                    f"下次刷新：{self.next_refresh:%Y-%m-%d %H:%M:%S}（约 {hours} 小时 {minutes} 分）",
                    f"Next refresh: {self.next_refresh:%Y-%m-%d %H:%M:%S} (about {hours}h {minutes}m)",
                ))
        self.root.after(1000, self._poll_events)

    def _show_summary(self, result):
        self.result = result
        all_assignments = [item for course in result["courses"] for item in course["assignments"]]
        overdue = sum(item.get("due_category") == "overdue" for item in all_assignments)
        week = sum(item.get("due_category") in {"due_24h", "due_3d", "due_7d"} for item in all_assignments)
        news = sum(
            item.get("is_new", False)
            for course in result["courses"] for item in course.get("announcements", [])
        )
        self.card_vars["courses"].set(str(len(result["courses"])))
        self.card_vars["overdue"].set(str(overdue))
        self.card_vars["week"].set(str(week))
        self.card_vars["news"].set(str(news))
        self._render_assignments()
        self._render_announcements()
        self._render_courses()

    @staticmethod
    def _display_time(value):
        parsed = parse_canvas_time(value)
        if not parsed:
            return "—"
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")

    def _clear_tree(self, tree):
        for item_id in tree.get_children():
            tree.delete(item_id)

    def _render_assignments(self):
        self._clear_tree(self.assignment_tree)
        if not self.result:
            return
        selected_filter = self.assignment_filter.get()
        filters = {
            t("近期未完成", "Upcoming incomplete"): {"overdue", "due_24h", "due_3d", "due_7d"},
            t("全部未完成", "All incomplete"): {"overdue", "due_24h", "due_3d", "due_7d", "later", "no_due"},
            t("已逾期", "Overdue"): {"overdue"}, t("24 小时内", "Within 24 hours"): {"due_24h"},
            t("3 天内", "Within 3 days"): {"due_3d"}, t("7 天内", "Within 7 days"): {"due_7d"},
            t("稍后截止", "Due later"): {"later"}, t("无截止时间", "No due date"): {"no_due"},
            t("已完成", "Completed"): {"completed"}, t("全部作业", "All assignments"): set(DUE_ORDER),
        }
        allowed = filters.get(selected_filter, set(DUE_ORDER))
        rows = []
        for course in self.result["courses"]:
            for assignment in course["assignments"]:
                if assignment.get("due_category") in allowed:
                    rows.append((course, assignment))
        rows.sort(key=lambda pair: (
            DUE_ORDER.index(pair[1].get("due_category", "no_due")),
            pair[1].get("due_at") or "9999",
            pair[1].get("title") or "",
        ))
        for index, (course, assignment) in enumerate(rows):
            category = assignment.get("due_category", "no_due")
            tag = "overdue" if category == "overdue" else "urgent" if category == "due_24h" else ""
            item_id = f"assignment-{index}"
            self.assignment_tree.insert("", "end", iid=item_id, values=(
                course["name"], DUE_LABELS.get(category, category), assignment["title"],
                self._display_time(assignment.get("due_at")),
                "—" if assignment.get("points_possible") is None else assignment["points_possible"],
            ), tags=(tag,) if tag else ())
            self.item_urls[(str(self.assignment_tree), item_id)] = assignment.get("url")

    def _render_announcements(self):
        self._clear_tree(self.news_tree)
        if not self.result:
            return
        rows = [
            (course, item) for course in self.result["courses"]
            for item in course.get("announcements", [])
        ]
        rows.sort(key=lambda pair: pair[1].get("posted_at") or "", reverse=True)
        self.announcement_items = {}
        for index, (course, item) in enumerate(rows):
            item_id = f"announcement-{index}"
            self.news_tree.insert("", "end", iid=item_id, values=(
                "NEW" if item.get("is_new") else "",
                course["name"], item["title"], item.get("author") or "—",
                self._display_time(item.get("posted_at")),
            ), tags=("new",) if item.get("is_new") else ())
            self.item_urls[(str(self.news_tree), item_id)] = item.get("url")
            self.announcement_items[item_id] = (course, item)

    def _render_courses(self):
        self._clear_tree(self.course_tree)
        if not self.result:
            return
        for index, course in enumerate(self.result["courses"]):
            done = sum(item.get("completed") for item in course["assignments"])
            pending = len(course["assignments"]) - done
            item_id = f"course-{index}"
            self.course_tree.insert("", "end", iid=item_id, values=(
                course["name"], course.get("term") or "—", len(course["assignments"]),
                pending, done, len(course.get("announcements", [])),
            ))
            self.item_urls[(str(self.course_tree), item_id)] = course.get("url")

    def _show_announcement_detail(self, _event=None):
        selected = self.news_tree.selection()
        if not selected:
            return
        course, item = self.announcement_items.get(selected[0], ({}, {}))
        text = (
            f"{item.get('title', '')}\n{course.get('name', '')} · "
            f"{item.get('author') or t('课程教师', 'Course instructor')} · {self._display_time(item.get('posted_at'))}\n\n"
            f"{item.get('message') or t('没有正文摘要', 'No message preview available')}"
        )
        self.news_detail.configure(state="normal")
        self.news_detail.delete("1.0", "end")
        self.news_detail.insert("1.0", text)
        self.news_detail.configure(state="disabled")

    def _open_selected_item(self, event):
        import webbrowser
        tree = event.widget
        item_id = tree.identify_row(event.y) or tree.focus()
        url = self.item_urls.get((str(tree), item_id))
        if url:
            webbrowser.open(url)

    def open_data_directory(self):
        if sys.platform == "win32":
            import os
            os.startfile(DATA_DIR)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(DATA_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])


def main():
    root = tk.Tk()
    CanvasAssistantApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
