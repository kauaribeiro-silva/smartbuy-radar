import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "SmartBuy Radar — Otimizador de Compra Perfeita",
  description:
    "Inteligência multimodal de compras no Brasil: menor preço à vista, melhor parcelado, cupons vivos e hack de pontos/cashback.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR" className="dark">
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} min-h-screen bg-radar-bg font-sans text-gray-100 antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
