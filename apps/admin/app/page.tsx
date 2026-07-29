import { redirect } from "next/navigation";

/**
 * Root route (`/`).
 *
 * The admin panel has no landing page of its own -- the weekly run wizard at
 * `/projections` is the only UI surface. Without this file the App Router has
 * no `page.tsx` at the segment root, so `/` fell through to the framework 404.
 *
 * `redirect()` in a server component issues the redirect during render, so the
 * browser never paints an intermediate page.
 */
export default function RootPage() {
  redirect("/projections");
}
