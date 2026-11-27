import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { Toaster } from "sonner";
import "./globals.css";

export const metadata: Metadata = { title: "Pigeon", description: "Team browser accounts, with a Reddit outreach agent" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        {children}
        <Toaster position="bottom-center" theme="system" toastOptions={{ classNames: { toast: "!rounded-xl !border-line !shadow-pop !font-sans" } }} />
      </body>
    </html>
  );
}
