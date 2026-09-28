# Canvas Local Assistant v1.4

A local Windows and macOS desktop app for Northwestern Canvas. It refreshes every five hours and displays all accessible courses, upcoming assignments, submission status, and recent instructor announcements.

## Features

- Dashboard cards for courses, overdue work, work due within seven days, and new announcements.
- Assignment filters for overdue, 24 hours, three days, seven days, later, no due date, completed, and all assignments.
- Instructor announcements from the last 30 days with new-item highlighting and message previews.
- Automatic five-hour refresh plus a manual refresh button.
- Double-click a course, assignment, or announcement to open it in Canvas.
- Canvas pagination and duplicate-assignment handling.

## Windows

Run `run_windows_en.bat` for English or `run_windows.bat` for Chinese.

The first run creates a local Python environment and installs Playwright Chromium. Keep the app window open for automatic refreshes.

## macOS

Install Python 3 from python.org with Tkinter support. Extract the ZIP, open Terminal in the extracted directory, then run:

```bash
bash run_mac_en.command
```

Use `bash run_mac.command` for Chinese.

## Sign-in and privacy

- Windows stores optional SSO credentials in Windows Credential Manager.
- macOS stores optional SSO credentials in Keychain.
- Credentials are filled only when the browser hostname is exactly `login.microsoftonline.com`.
- The app reuses a private local browser profile and automatically accepts Microsoft's stay-signed-in prompt.
- Northwestern may still require a Duo approval under its security policy.
- `browser_profile`, generated course data, virtual environments, and caches are excluded by `.gitignore`.
- Never share your local `browser_profile` or `data` directory.

## Output

The latest machine-readable and text snapshots are written to `data/latest.json` and `data/latest.txt`.
