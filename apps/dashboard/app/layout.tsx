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
      // Read from env so a deployment can override, but fall back to
      // "/dashboard" in code. NEXT_PUBLIC_* values are inlined at BUILD time,
      // so a Droplet that sets them only as runtime env would otherwise get
      // `undefined` here and Clerk would fall back to its own default.
      signInFallbackRedirectUrl={
        process.env.NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL ?? "/dashboard"
      }
      signUpFallbackRedirectUrl={
        process.env.NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL ?? "/dashboard"
      }
      appearance={{
        // Kept in lockstep with the tokens in app/globals.css: Clerk renders
        // its own DOM, so the sign-in modal is the one surface Tailwind cannot
        // reach. Values mirror --primary / --card / --background / --foreground
        // and the 3px radius scale.
        variables: {
          colorPrimary: '#FF5F1F',
          colorBackground: '#09090B',
          colorForeground: '#FAFAFA',
          colorInput: '#000000',
          colorInputForeground: '#FAFAFA',
          borderRadius: '3px',
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
