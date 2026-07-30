import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Clerk middleware.
 *
 * Route posture for this app:
 *
 *   /            the projections dashboard  -- PROTECTED
 *   /dashboard   alias that redirects to /  -- PROTECTED
 *   /api/*       BFF proxy routes           -- not gated here, see below
 *
 * There is no public marketing page inside this Next app to exempt: the
 * landing site is a separate static deployment served by nginx at the apex
 * domain, while this app is only ever reached at app.<domain>. So every page
 * route is private and the "public route" list is deliberately empty.
 *
 * API routes are excluded on purpose. `auth.protect()` answers an
 * unauthenticated request with a 307 to Clerk's hosted sign-in, which is the
 * right response for a page and the wrong one for fetch(): the caller would
 * receive an HTML redirect instead of JSON. Those handlers already attach the
 * session token and the FastAPI layer verifies it and fails closed, so an
 * unauthenticated call gets a clean 401 from the backend instead.
 *
 * Page-level `auth.protect()` in app/page.tsx is kept as well. Two
 * independent gates on the same surface is intentional -- a middleware
 * matcher regression should not silently expose the data.
 */
const isApiRoute = createRouteMatcher(["/api(.*)"]);

export default clerkMiddleware(async (auth, request) => {
  if (isApiRoute(request)) return;
  await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next internals and static assets: Clerk's Edge runtime should not
    // run on files that can never carry a session.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
