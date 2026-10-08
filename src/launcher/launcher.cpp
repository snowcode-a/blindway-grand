// ===========================================================================
//  launcher.cpp  —  Blindway Grand 启动器 (Visual Studio 2022)
//
//  为什么需要它：
//    界面本体是 Python + PyQt5，而 Visual Studio 2022 默认**不带** Python
//    工作负载（本机确认未安装）。直接给 .py 建 vcxproj 是打不开的。
//    所以这里做一个极简 C++ 控制台程序：由它去拉起 Python 界面进程。
//    这样在 VS2022 里按 F5 就能直接运行整个系统，不需要装任何额外工作负载。
//
//  行为：
//    1. 以 UTF-8 输出中文到 VS 的「输出」窗口
//    2. 定位 python.exe（优先用写死的解释器，其次 PATH 里的 python）
//    3. 拉起 qt_app.py，并把工作目录设为项目根，让界面能找到 detector.py
//    4. 阻塞等待，界面关闭后打印退出码
// ===========================================================================

#include <windows.h>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

namespace {

// ---------------------------------------------------------------------------
// 用 CreateProcessW 拉起进程并等待结束
// ---------------------------------------------------------------------------
int RunAndWait(const std::wstring &cmdLine, const std::wstring &workDir)
{
    std::vector<wchar_t> buf(cmdLine.begin(), cmdLine.end());
    buf.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};

    BOOL ok = CreateProcessW(
        nullptr,
        buf.data(),
        nullptr, nullptr,
        FALSE,
        CREATE_UNICODE_ENVIRONMENT,
        nullptr,
        workDir.empty() ? nullptr : workDir.c_str(),
        &si, &pi);

    if (!ok) {
        std::wcerr << L"[错误] CreateProcessW 失败, GetLastError = "
                   << GetLastError() << std::endl;
        return -1;
    }

    WaitForSingleObject(pi.hProcess, INFINITE);

    DWORD exitCode = 0;
    GetExitCodeProcess(pi.hProcess, &exitCode);

    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return static_cast<int>(exitCode);
}

// ---------------------------------------------------------------------------
// 立即把一行诊断信息写到文件。
// 为什么要写文件：从 VS 或上层工具启动时，stdout 会被重定向缓冲，
// 万一卡住就什么都看不到。日志文件能精确定位卡在哪一步。
// ---------------------------------------------------------------------------
void LogLine(const std::wstring &dir, const std::wstring &text)
{
    std::wstring path = dir + L"\\launcher_log.txt";
    HANDLE h = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE)
        return;
    SYSTEMTIME st{};
    GetLocalTime(&st);
    wchar_t stamp[64]{};
    swprintf_s(stamp, L"[%02d:%02d:%02d] ", st.wHour, st.wMinute, st.wSecond);
    std::wstring line = std::wstring(stamp) + text + L"\r\n";
    // 写 UTF-8，记事本/VS 打开都不乱码
    int need = WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(),
                                   nullptr, 0, nullptr, nullptr);
    if (need > 0) {
        std::string utf8(need, '\0');
        WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(),
                            utf8.data(), need, nullptr, nullptr);
        DWORD written = 0;
        WriteFile(h, utf8.data(), (DWORD)utf8.size(), &written, nullptr);
    }
    CloseHandle(h);
}

// ---------------------------------------------------------------------------
// 文件是否存在
// ---------------------------------------------------------------------------
bool FileExists(const std::wstring &path)
{
    DWORD attr = GetFileAttributesW(path.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

// ---------------------------------------------------------------------------
// 这个解释器装了本项目要用的依赖吗？
//
// 为什么必须查：机器上常常有好几个 Python，PATH 里那个通常是裸装的
// （没有 torch / PyQt5）。只按"文件存在"选，会挑到裸 Python，
// 界面一起来就 ImportError 退出码 1 —— 用户看到的就是"程序闪退"。
// 这里跑一句 import 探一下，装没装立刻见分晓。
// ---------------------------------------------------------------------------
bool HasDeps(const std::wstring &python)
{
    std::wstring cmd = L"\"" + python + L"\" -c \"import torch,cv2,PyQt5\"";
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;          // 别闪黑窗口
    PROCESS_INFORMATION pi{};

    if (!CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi)) {
        return false;
    }
    WaitForSingleObject(pi.hProcess, 20000);   // 最多等 20 秒
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return code == 0;
}

// ---------------------------------------------------------------------------
// 取 launcher.exe 所在目录
// ---------------------------------------------------------------------------
std::wstring ExeDir()
{
    wchar_t buf[MAX_PATH * 2]{};
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH * 2);
    std::wstring p(buf, n);
    size_t pos = p.find_last_of(L"\\/");
    return (pos == std::wstring::npos) ? L"." : p.substr(0, pos);
}

} // namespace

// ===========================================================================
int main()
{
    // VS 的「输出」窗口按系统代码页解码，显式设成 UTF-8 才不会出乱码
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    std::wstring exe_dir = ExeDir();
    LogLine(exe_dir, L"启动器开始运行, exe 目录 = " + exe_dir);

    std::wcout << L"======================================================" << std::endl;
    std::wcout << L"  Blindway Grand  ·  盲道占用检测  ·  启动器" << std::endl;
    std::wcout << L"======================================================" << std::endl;

    // ---- 1. 定位解释器 -------------------------------------------------
    // 按优先级找一个能用的 python.exe：
    //   1) 环境变量 BLINDWAY_PYTHON（想指定虚拟环境时设这个）
    //   2) 几个常见安装位置
    //   3) 交给 PATH 里的 python
    // 不再写死某一台机器的绝对路径 —— 那样别人 clone 下来就得改代码。
    std::wstring python;

    wchar_t envbuf[MAX_PATH * 2] = {0};
    DWORD envlen = GetEnvironmentVariableW(L"BLINDWAY_PYTHON", envbuf,
                                           MAX_PATH * 2);
    if (envlen > 0 && envlen < MAX_PATH * 2 && FileExists(envbuf)) {
        python = envbuf;
        std::wcout << L"[1/3] 解释器(来自 BLINDWAY_PYTHON): " << python
                   << std::endl;
        LogLine(exe_dir, L"[1/3] 解释器 = " + python + L" (环境变量)");
    }

    if (python.empty()) {
        // 常见位置：WorkBuddy 默认环境、用户级安装、conda、系统安装。
        // %USERPROFILE% / %LOCALAPPDATA% 运行时展开，不含固定用户名。
        const wchar_t *kCandidates[] = {
            L"%USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\default"
            L"\\Scripts\\python.exe",
            L"%LOCALAPPDATA%\\Programs\\Python\\Python313\\python.exe",
            L"%LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe",
            L"%LOCALAPPDATA%\\Programs\\Python\\Python311\\python.exe",
            L"%USERPROFILE%\\anaconda3\\python.exe",
            L"%USERPROFILE%\\miniconda3\\python.exe",
            L"C:\\Python313\\python.exe",
            L"C:\\Python312\\python.exe",
            L"C:\\Python311\\python.exe",
        };
        // 逐个试：文件存在 **而且** 装了依赖才用。
        // 只判存在会挑到系统里的裸 Python，界面一启动就 ImportError 闪退。
        std::wstring existsButNoDeps;
        for (const wchar_t *cand : kCandidates) {
            wchar_t expanded[MAX_PATH * 2] = {0};
            DWORD n = ExpandEnvironmentStringsW(cand, expanded, MAX_PATH * 2);
            if (n == 0 || n >= MAX_PATH * 2 || !FileExists(expanded)) {
                continue;
            }
            if (HasDeps(expanded)) {
                python = expanded;
                break;
            }
            if (existsButNoDeps.empty()) {
                existsButNoDeps = expanded;   // 记下来，稍后给个明确提示
            }
        }
        if (python.empty() && !existsButNoDeps.empty()) {
            std::wcout << L"[警告] 找到的解释器缺少依赖(需 torch/cv2/PyQt5): "
                       << existsButNoDeps << std::endl;
            LogLine(exe_dir, L"[1/3] 候选解释器缺依赖 = " + existsButNoDeps);
        }
    }

    if (python.empty()) {
        // 再给 PATH 一次机会，同样要过依赖检查
        if (HasDeps(L"python")) {
            python = L"python";
            std::wcout << L"[1/3] 解释器: python (来自 PATH，依赖已就绪)"
                       << std::endl;
            LogLine(exe_dir, L"[1/3] 解释器 = python (PATH)");
        } else {
            std::wcout << L"[错误] 没找到装好依赖的 Python 解释器。"
                       << std::endl;
            std::wcout << L"       请先安装依赖：pip install -r requirements.txt"
                       << std::endl;
            std::wcout << L"       或用环境变量指定：set BLINDWAY_PYTHON=<python.exe 的完整路径>"
                       << std::endl;
            LogLine(exe_dir, L"[错误] 未找到装好依赖的解释器, 退出");
            return 3;
        }
    } else {
        std::wcout << L"[1/3] 解释器: " << python << std::endl;
        LogLine(exe_dir, L"[1/3] 解释器 = " + python);
    }

    // ---- 2. 定位工程根目录 ---------------------------------------------
    // 编译产物在 <root>\launcher\... 下（bin\Debug），需要向上找到含 qt_app.py 的目录
    std::wstring dir = exe_dir;
    std::wstring root = dir;
    bool found = false;
    for (int i = 0; i < 6; ++i) {
        if (FileExists(root + L"\\qt_app.py")) {
            found = true;
            break;
        }
        size_t pos = root.find_last_of(L"\\/");
        if (pos == std::wstring::npos)
            break;
        root = root.substr(0, pos);
    }
    if (!found) {
        std::wcout << L"[警告] 未能向上定位到 qt_app.py，回退为可执行文件所在目录" << std::endl;
        LogLine(exe_dir, L"[2/3] 警告: 未找到 qt_app.py, 回退到 exe 目录");
        root = dir;
    }
    std::wcout << L"[2/3] 工程根目录: " << root << std::endl;
    LogLine(exe_dir, L"[2/3] 工程根目录 = " + root);

    if (!FileExists(root + L"\\qt_app.py")) {
        std::wcout << L"[错误] 在工程根目录下找不到 qt_app.py，无法启动。" << std::endl;
        std::wcout << L"       请确认 launcher 工程与 qt_app.py 的相对位置没有被改动。" << std::endl;
        LogLine(exe_dir, L"[错误] 工程根目录下没有 qt_app.py, 退出");
        return 2;
    }

    // ---- 3. 拉起界面 ---------------------------------------------------
    // 直接创建 python 进程，**不经过 PowerShell 管道**。
    // 中间套一层 shell 时，如果父进程的输出被重定向（从 VS 启动、或被上层工具
    // 捕获），子进程有概率卡在启动阶段 —— 实测踩到过：进程树里只有 powershell，
    // python 根本没起来。直接起进程最稳。
    // 中文输出靠 PYTHONIOENCODING + 本进程已设的 UTF-8 代码页保证不乱码。
    std::wstring cmd = L"\"" + python + L"\" qt_app.py";

    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"QT_LOGGING_RULES", L"qt.qpa.fonts=false");

    std::wcout << L"[3/3] 正在启动界面 (首次加载 YOLO 模型约 5~15 秒) ..." << std::endl;
    std::wcout << L"      命令行: " << cmd << std::endl;
    std::wcout << L"      工作目录: " << root << std::endl;
    std::wcout << L"------------------------------------------------------" << std::endl;
    std::wcout.flush();
    // 子进程共享同一个 stdout，先把已有内容推出去，免得被缓冲吞掉
    std::fflush(stdout);
    LogLine(exe_dir, L"[3/3] 准备启动: " + cmd);

    int code = RunAndWait(cmd, root);

    LogLine(exe_dir, L"[完成] 界面退出码 = " + std::to_wstring(code));

    std::wcout << L"------------------------------------------------------" << std::endl;
    if (code == 0) {
        std::wcout << L"界面已正常退出。" << std::endl;
    } else {
        std::wcout << L"[注意] 界面退出码 = " << code << L"（非 0 表示异常退出）" << std::endl;
    }
    return code;
}
