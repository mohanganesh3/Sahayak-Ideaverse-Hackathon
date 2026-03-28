import type { Metadata, Viewport } from "next"
import { Toaster } from "sonner"
import "./globals.css"

export const metadata: Metadata = {
  title: "SAHAYAK – Medication Safety Assistant",
  description:
    "AI-powered medication safety for elderly Indian patients. Check your medicines for harmful interactions in your language.",
  applicationName: "SAHAYAK",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "SAHAYAK",
  },
  formatDetection: { telephone: false },
  openGraph: {
    title: "SAHAYAK – Medication Safety",
    description: "Safe medicine check for elderly patients",
    type: "website",
  },
}

export const viewport: Viewport = {
  themeColor: "#0D9488",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="h-full">
      <head>
        <meta name="mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <link rel="apple-touch-icon" href="/icons/icon-192.png" />
      </head>
      <body className="min-h-full flex flex-col antialiased">
        {children}
        <Toaster
          richColors
          position="top-center"
          toastOptions={{
            style: {
              fontSize: "1rem",
              fontFamily: "var(--font-sans)",
            },
          }}
        />
      </body>
    </html>
  )
}
