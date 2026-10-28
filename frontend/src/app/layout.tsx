import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "пО!льский чемоданчик — планировщик путешествий",
  description: "Планируйте путешествия с пО!льским чемоданчиком",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ru"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
