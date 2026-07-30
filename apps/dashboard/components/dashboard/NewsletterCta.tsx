/**
 * NewsletterCta -- parity with the live site's "Stay Updated" section.
 *
 * Server component: there is no email-capture backend in this stack, and the
 * live site delegates to Substack, so this links out rather than pretending to
 * own a subscriber list. Destinations are the live site's own.
 */

import { Instagram, Mail, Send } from "lucide-react";

const SUBSTACK = "https://substack.com/@kibasfpltreats";
const INSTAGRAM = "https://instagram.com/kibasfpltreats";
const EMAIL = "mailto:gautamdivyansh.work@gmail.com";

export default function NewsletterCta() {
  return (
    <section className="grid gap-4 border border-border bg-card p-5 sm:grid-cols-2">
      <div>
        <h3 className="text-sm font-semibold">Weekly Newsletter</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Weekly xG + xA rankings, fixture analysis and top transfer targets —
          one email before every deadline. No spam, ever.
        </p>
        <a
          href={SUBSTACK}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90"
        >
          <Send className="h-3.5 w-3.5" aria-hidden />
          Subscribe on Substack
        </a>
      </div>

      <div className="sm:border-l sm:border-border sm:pl-5">
        <h3 className="text-sm font-semibold">Connect</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          DM for feedback, ideas, or collaborations.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            href={INSTAGRAM}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:text-foreground"
          >
            <Instagram className="h-3.5 w-3.5" aria-hidden /> Instagram
          </a>
          <a
            href={EMAIL}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:text-foreground"
          >
            <Mail className="h-3.5 w-3.5" aria-hidden /> Email
          </a>
        </div>
      </div>
    </section>
  );
}
