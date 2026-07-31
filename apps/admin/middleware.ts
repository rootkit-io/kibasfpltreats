import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

type SessionClaims = Record<string, unknown> | null | undefined;

function normalized(value: unknown): string | null {
  return typeof value === "string" && value.trim()
    ? value.trim().toLowerCase()
    : null;
}

function claimEmail(claims: SessionClaims): string | null {
  if (!claims) return null;
  return normalized(claims.email) ?? normalized(claims.email_address);
}

/**
 * Server-side authorization policy for every admin page and BFF route.
 *
 * Email must be added to Clerk session-token claims as `email` or
 * `email_address` when ADMIN_EMAIL is used. ADMIN_ROLE uses Clerk's active
 * organization role (for example, `org:admin`). At least one allowlist must
 * be configured; missing configuration denies access rather than opening it.
 */
export default clerkMiddleware(async (auth) => {
  const session = await auth.protect();
  const allowedEmail = normalized(process.env.ADMIN_EMAIL);
  const allowedRole = normalized(process.env.ADMIN_ROLE);

  if (!allowedEmail && !allowedRole) {
    return NextResponse.json(
      { error: "admin authorization is not configured" },
      { status: 503 },
    );
  }

  const emailAllowed =
    allowedEmail !== null && claimEmail(session.sessionClaims) === allowedEmail;
  const roleAllowed =
    allowedRole !== null && normalized(session.orgRole) === allowedRole;

  if (!emailAllowed && !roleAllowed) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
});

export const config = {
  matcher: [
    // Run on pages and API routes. Only framework internals/static assets skip
    // Clerk because they cannot invoke privileged admin behavior.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
