import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kiba's FPL Treats — Projections Dashboard",
  description:
    "Published FPL expected-points and Monte Carlo projections, every gameweek.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: '#FF5F1F',
          colorBackground: '#0A0E15',
          colorForeground: '#F2F5F7',
          colorInput: '#05070B',
          colorInputForeground: '#F2F5F7',
          borderRadius: '0.5rem',
        },
      }}
    >
      <html lang="en">
        <body className="min-h-screen bg-background text-foreground antialiased">
          {children}
        </body>
      </html>
    </ClerkProvider>
  );
}
