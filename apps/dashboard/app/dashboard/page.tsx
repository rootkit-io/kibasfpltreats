import { redirect } from "next/navigation";

/**
 * `/dashboard` -> `/`
 *
 * This app IS the dashboard: it is served on its own subdomain
 * (app.<domain> -> dashboard:3000 in the Caddyfile), so the grid lives at the
 * root and there is no second page to route between.
 *
 * The stub exists so that Clerk's configured redirect targets --
 * NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/dashboard and its SIGN_UP
 * counterpart -- resolve instead of landing a freshly authenticated user on a
 * 404. Anything already linking to /dashboard keeps working too.
 *
 * `replace` semantics are implicit in redirect(): the stub does not appear in
 * session history, so Back from the dashboard returns to the referrer rather
 * than bouncing through here.
 */
export default function DashboardAlias() {
  redirect("/");
}
