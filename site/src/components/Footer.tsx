import { ArrowUp } from "lucide-react";
import { Lockup } from "./Lockup";
import { links, reposPublic } from "@/content/site";
import { asset } from "@/lib/paths";

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-band">
        <div className="wrap">
          <Lockup />
          <p className="tag-line">
            Your game. <span>Your rules.</span>
          </p>
        </div>
      </div>
      <div className="wrap footer-cols">
        <p>
          Project 8 Plus is an unofficial fan project, not affiliated with or endorsed by Activision or Neversoft. You
          need your own copy of Tony Hawk&rsquo;s Project 8 for PS3.
        </p>
        <nav aria-label="Footer">
          {reposPublic ? (
            <>
              <a href={links.repo} rel="noopener">
                GitHub
              </a>
              <a href={links.issues} rel="noopener">
                Report a problem
              </a>
            </>
          ) : null}
          <a href={asset("docs/#legal")}>Legal</a>
          <a href="#top">
            <ArrowUp size={13} aria-hidden className="inline-icon" /> Top
          </a>
        </nav>
      </div>
    </footer>
  );
}
