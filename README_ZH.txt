Canvas Local Assistant v1.4 (Windows + macOS)

功能：
- 启动后立即读取一次 Canvas。
- 此后每 5 小时自动刷新。
- 通过 Canvas API 读取全部可访问课程，不再局限于首页课程卡片。
- 每门课读取全部作业，并自动处理 API 分页和重复记录。
- 作业按“逾期、24 小时内、3 天内、7 天内、稍后、无截止时间、已完成”分类。
- 根据 Canvas submission 状态区分未完成与已提交作业。
- 读取每门课最近 30 天公告，并标记相对上次刷新出现的新公告。
- 首页卡片显示课程数、逾期数、未来 7 天作业数和新公告数。
- “作业与截止日期”标签可筛选近期、全部未完成、各截止区间、无截止日期和已完成作业。
- “教师公告”标签显示公告列表和正文详情。
- “全部课程”标签汇总每门课的作业、完成情况与公告数量。
- 双击作业、公告或课程可直接在浏览器中打开对应 Canvas 页面。
- 界面中可以随时点击“立即刷新”。
- 结果写入 data\latest.txt 和 data\latest.json。

Windows 使用方法：
1. 双击 run_windows.bat。
2. 第一次运行或登录过期时，会自动打开 Chromium。
3. 在浏览器里正常完成 Northwestern SSO 登录。
4. 登录成功后，程序会自动继续，不需要回到命令行按 Enter。
5. 保持 Canvas Local Assistant 窗口运行，它会每 5 小时刷新一次。

macOS 使用方法：
1. 安装 Python 3（推荐从 python.org 安装，需包含 Tkinter）。
2. 解压 ZIP；不要直接在压缩包预览中运行。
3. 打开 Terminal，将 run_mac.command 拖入终端后按回车。
4. 如果 macOS 阻止运行，执行：bash run_mac.command
5. 首次启动会自动建立环境并安装 Chromium，之后正常启动应用。
6. 保持应用窗口运行，它会每 5 小时刷新一次。

登录设置：
- 首次运行会询问 Northwestern/Microsoft SSO 账户邮箱和密码。
- Windows 使用凭据管理器；macOS 使用系统 Keychain。
- 凭据由当前系统用户保护，不存放在应用目录。
- 密码不会写入项目文件、latest.json、latest.txt 或源代码。
- 只有网页域名严格为 login.microsoftonline.com 时才会自动填写。
- 可通过应用中的“登录设置”修改或删除已保存的登录信息。
- 学校强制要求 Duo 时，仍需由用户完成 Duo 确认。

隐私说明：
- 程序不会在代码里保存 Northwestern 密码。
- browser_profile 保存本机登录 Cookie，必须保持私密。
- data 目录中的结果包含课程和作业信息，也应保持私密。
- .gitignore 已排除上述私人数据，发布代码时不要手动上传这些目录或文件。

注意：
- 关闭应用后，自动刷新也会停止。
- Windows 休眠期间不会运行；电脑唤醒且应用仍开着后，会继续检查并刷新。
- 如果某门课不允许访问 Assignments API，界面和输出会为该课程显示错误，不会影响其他课程。
- 程序会复用 browser_profile 中的 SSO 会话；只要学校会话仍有效，自动刷新不会要求登录。
- Duo 是否再次出现由 Northwestern 的安全策略决定，程序不会绕过 Duo。
