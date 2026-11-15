import React from "react";
import { Show, SignInButton, SignUpButton, UserButton, useAuth, useUser } from "@clerk/react";
import { LogIn } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { api, setAuthTokenProvider } from "./api";
import { getLanguage } from "./langState";

/**
 * Clerk Auth — tích hợp sẵn nhưng CHỈ kích hoạt khi có key.
 *
 * Key lấy từ biến build-time VITE_CLERK_PUBLISHABLE_KEY (frontend/.env).
 * - Có key: main.jsx bọc app bằng ClerkProvider; khu vực này hiện nút
 *   Đăng nhập / Đăng ký mở modal Clerk (đăng nhập Google là lựa chọn chính
 *   khi đã bật Google provider trong Clerk Dashboard), sau đăng nhập hiện
 *   UserButton.
 * - Chưa có key: KHÔNG mount ClerkProvider (tránh lỗi runtime), nút
 *   "Đăng nhập" chỉ mở một thông báo gọn cho biết auth chưa cấu hình.
 *   App chạy hoàn toàn bình thường, không nội dung nào bị khoá sau đăng
 *   nhập — bản demo công khai vào thẳng được.
 */
export const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "";
export const clerkEnabled = Boolean(CLERK_KEY);

const pill =
  "flex h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium text-zinc-600 transition-colors hover:text-foreground dark:text-zinc-300";
const pillPrimary =
  "flex h-9 items-center gap-1.5 rounded-full bg-zinc-900 px-3.5 text-[13px] font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200";

/**
 * Đồng bộ phiên Clerk về backend (chỉ mount khi clerkEnabled):
 * - đăng ký provider để api.js lấy session token cho mọi lời gọi ask;
 * - ngay khi đăng nhập, gọi POST /api/me để backend upsert hồ sơ người dùng
 *   (id/email/tên/ảnh từ Clerk) vào bảng users trong Supabase.
 */
export function AuthSync() {
  const { isSignedIn, getToken } = useAuth();
  const { user } = useUser();
  const syncedRef = React.useRef(false);
  React.useEffect(() => {
    setAuthTokenProvider(() => getToken());
  }, [getToken]);
  React.useEffect(() => {
    if (isSignedIn && user && !syncedRef.current) {
      syncedRef.current = true;
      api.syncMe({
        email: user.primaryEmailAddress?.emailAddress || undefined,
        name: user.fullName || undefined,
        imageUrl: user.imageUrl || undefined,
        language: getLanguage(),
      }).catch(() => { syncedRef.current = false; });
    }
    if (!isSignedIn) syncedRef.current = false;
  }, [isSignedIn, user]);
  return null;
}

/**
 * Cổng đăng nhập: chưa đăng nhập thì phủ một lớp chặn toàn màn hình — không
 * dùng được chat hay bất kỳ chức năng nào cho tới khi đăng nhập (yêu cầu của
 * chủ sản phẩm). Modal Clerk mở phía trên lớp chặn này.
 */
export function AuthGate() {
  return (
    <Show when="signed-out">
      <div className="fixed inset-0 z-30 grid place-items-center bg-[#f7f5f0]/90 p-4 backdrop-blur-sm dark:bg-[#050d2a]/90">
        <div className="w-full max-w-sm rounded-3xl border bg-white p-6 text-center shadow-xl dark:bg-zinc-900">
          <div
            className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-full border-[3px] border-white text-2xl text-[#2b313b] shadow-[0_0_0_1px_#e7e5e1,0_2px_10px_rgba(0,0,0,0.11)]"
            style={{ background: "radial-gradient(circle at 35% 22%,#fff 3%,#f1eee6 59%,#dedcd6 100%)" }}
          >✳</div>
          <h2 className="text-lg font-semibold">Đăng nhập để sử dụng Flame</h2>
          <p className="mt-1 text-[13px] leading-relaxed text-zinc-500 dark:text-zinc-400">
            Ứng dụng yêu cầu đăng nhập. Lịch sử trò chuyện của bạn được lưu theo
            tài khoản để agent ghi nhớ các phiên làm việc trước.
          </p>
          <SignInButton mode="modal">
            <button className="mt-4 w-full rounded-full bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200">
              Đăng nhập bằng Google
            </button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="mt-2 w-full rounded-full border px-4 py-2.5 text-sm font-medium text-zinc-600 transition-colors hover:text-foreground dark:text-zinc-300">
              Đăng ký tài khoản mới
            </button>
          </SignUpButton>
        </div>
      </div>
    </Show>
  );
}

export function AuthArea() {
  const [notice, setNotice] = React.useState(false);

  if (clerkEnabled) {
    return (
      <div className="flex items-center gap-1.5">
        <Show when="signed-out">
          <SignInButton mode="modal">
            <button className={pillPrimary} title="Đăng nhập">
              <LogIn className="h-4 w-4" /> Đăng nhập
            </button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className={pill} title="Đăng ký">Đăng ký</button>
          </SignUpButton>
        </Show>
        <Show when="signed-in">
          <UserButton />
        </Show>
      </div>
    );
  }

  return (
    <>
      <button onClick={() => setNotice(true)} title="Đăng nhập" aria-label="Đăng nhập" className={pill}>
        <LogIn className="h-4 w-4" /> Đăng nhập
      </button>
      <Dialog open={notice} onOpenChange={setNotice}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Đăng nhập</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-[14px] leading-relaxed text-zinc-600 dark:text-zinc-300">
            <p>
              Đăng nhập bằng tài khoản Google (qua Clerk) đã được tích hợp sẵn
              nhưng <strong className="text-foreground">chưa kích hoạt</strong> —
              bản chạy này chưa có Clerk key trong cấu hình.
            </p>
            <p>
              Mọi nội dung vẫn dùng bình thường không cần đăng nhập. Để bật
              thật: tạo ứng dụng tại clerk.com, bật nhà cung cấp Google, rồi
              đặt biến <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[12px] dark:bg-white/10">VITE_CLERK_PUBLISHABLE_KEY</code> và
              build lại (hướng dẫn chi tiết: docs/AUTH_CLERK.md).
            </p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
