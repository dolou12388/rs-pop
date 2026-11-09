// 最小验证：Rust + Win32 原生浮层是否可行，以及进程内存基线
// 做三件事：全局鼠标状态检测拖选、创建不抢焦点的置顶浮窗、测量自身开销

use std::ffi::c_void;
use std::mem::zeroed;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use windows::core::w;
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{BeginPaint, CreateSolidBrush, DeleteObject, EndPaint, FillRect, PAINTSTRUCT};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetClientRect, GetMessageW, MessageBoxW,
    PostMessageW, PostQuitMessage, RegisterClassW, SetTimer, SetWindowPos, ShowWindow,
    TranslateMessage, CS_HREDRAW, CS_VREDRAW, HWND_TOPMOST, MB_ICONERROR, MSG, SWP_NOACTIVATE,
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, SW_SHOWNOACTIVATE, WM_DESTROY, WM_PAINT, WM_TIMER,
    WNDCLASSW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP,
};

const TIMER_MOUSE: usize = 1;
const TIMER_TOPMOST: usize = 2;

static DRAG_HINT: AtomicBool = AtomicBool::new(false);
static PILL_HWND: Mutex<isize> = Mutex::new(0);
static TIMER_COST_US: Mutex<u64> = Mutex::new(0);

fn main() {
    unsafe {
        let hinstance = match GetModuleHandleW(None) {
            Ok(h) => h,
            Err(_) => return,
        };

        let class_name = w!("RsPopPillClass");

        let mut wc: WNDCLASSW = zeroed();
        wc.lpfnWndProc = Some(wnd_proc);
        wc.hInstance = hinstance.into();
        wc.lpszClassName = class_name;
        wc.hbrBackground = CreateSolidBrush(COLORREF(0x00222022));
        wc.style = CS_HREDRAW | CS_VREDRAW;

        if RegisterClassW(&wc) == 0 {
            MessageBoxW(None, w!("register class failed"), w!("error"), MB_ICONERROR);
            return;
        }

        let hwnd = match CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            class_name,
            w!("RsPopPill"),
            WS_POPUP,
            100,
            100,
            248,
            44,
            None,
            None,
            Some(hinstance.into()),
            None,
        ) {
            Ok(h) => h,
            Err(_) => return,
        };

        *PILL_HWND.lock().unwrap() = hwnd.0 as isize;

        // 只置顶、不激活、不抢焦点 —— 与 Electron 版 showInactive 等价
        let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        let _ = SetWindowPos(
            hwnd,
            Some(HWND_TOPMOST),
            100,
            100,
            248,
            44,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        );

        let _ = SetTimer(Some(hwnd), TIMER_MOUSE, 16, None);
        let _ = SetTimer(Some(hwnd), TIMER_TOPMOST, 3000, None);

        println!("=== Rust native pill started ===");
        println!("hwnd = 0x{:X}", hwnd.0 as usize);
        println!("drag detect: 16ms timer, GetAsyncKeyState");
        println!("measuring baseline, auto exit in 10s\n");

        thread::spawn(|| {
            thread::sleep(Duration::from_secs(10));
            let acc = *TIMER_COST_US.lock().unwrap();
            println!("timer cost over 10s: {} us total ({} us/s)", acc, acc / 10);
            let h = *PILL_HWND.lock().unwrap();
            if h != 0 {
                let _ = PostMessageW(
                    Some(HWND(h as *mut c_void)),
                    WM_DESTROY,
                    WPARAM(0),
                    LPARAM(0),
                );
            }
        });

        let mut msg: MSG = zeroed();
        while GetMessageW(&mut msg, None, 0, 0).into() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

unsafe extern "system" fn wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match msg {
        WM_PAINT => {
            let mut ps: PAINTSTRUCT = zeroed();
            let hdc = BeginPaint(hwnd, &mut ps);
            let mut rc: RECT = zeroed();
            let _ = GetClientRect(hwnd, &mut rc);
            let brush = CreateSolidBrush(COLORREF(0x00222022));
            FillRect(hdc, &rc, brush);
            let _ = DeleteObject(brush.into());
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        WM_TIMER => {
            let t0 = Instant::now();
            if wparam.0 == TIMER_MOUSE {
                let down = (GetAsyncKeyState(VK_LBUTTON.0 as i32) as u16 & 0x8000) != 0;
                let was = DRAG_HINT.swap(down, Ordering::Relaxed);
                if was && !down {
                    println!("[drag end] mouse released");
                }
            } else if wparam.0 == TIMER_TOPMOST {
                let h = *PILL_HWND.lock().unwrap();
                if h != 0 {
                    let _ = SetWindowPos(
                        HWND(h as *mut c_void),
                        Some(HWND_TOPMOST),
                        0,
                        0,
                        0,
                        0,
                        SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
                    );
                }
            }
            let dt = t0.elapsed().as_micros() as u64;
            let mut acc = TIMER_COST_US.lock().unwrap();
            *acc += dt;
            LRESULT(0)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}
