import heroPoster from "./assets/hero-poster.webp";
import heroVideo from "./assets/hero-video.mp4";
import spaceAppsLogo from "./assets/nsa-logo.svg";
import type { Messages } from "./messages";
import { viewerHref } from "./viewer/viewState";
import "./Landing.css";

export function Landing({ t, isAbout, exploreHref }: {
  t: Messages;
  isAbout: boolean;
  exploreHref: string;
}) {
  return (
    <>
      <main className={isAbout ? "landing-hero landing-about" : "landing-hero"}>
        {!isAbout && (
          <video
            className="landing-video"
            src={heroVideo}
            poster={heroPoster}
            autoPlay
            muted
            loop
            playsInline
            aria-hidden="true"
          />
        )}
        <div className="landing-content">
          <h1>{isAbout ? t.aboutTitle : t.welcome}</h1>
          {isAbout ? (
            <div className="landing-about-copy">
              <p>{t.aboutIntro}</p>
              <p>{t.aboutExplore}</p>
            </div>
          ) : (
            <p className="landing-tagline">{t.intro}</p>
          )}
          <a className="button landing-cta" href={exploreHref}>
            {t.open} <span aria-hidden="true">→</span>
          </a>
          {!isAbout && (
            <div className="landing-samples">
              <a href={viewerHref(undefined, 1, "m31")}>{t.m31Field}</a>
              <a href={viewerHref()}>{t.scopeField}</a>
            </div>
          )}
        </div>
      </main>
      <footer className="landing-footer">
        <div className="landing-credits">
          <a className="landing-partner" href="https://www.spaceappschallenge.org">
            <img src={spaceAppsLogo} alt="NASA Space Apps Challenge" width={561} height={221} />
          </a>
          <div className="landing-project">
            <strong>StarDrift</strong>
            <p>{t.hackathon}</p>
          </div>
          <div>
            <strong>{t.dataAttribution}</strong>
            <p><a href="https://irsa.ipac.caltech.edu/Missions/spherex.html">{t.archive}</a></p>
          </div>
          <div>
            <strong>{t.sourceCode}</strong>
            <p><a href="https://github.com/hermes77-7/StarDrift">{t.viewSource} <span aria-hidden="true">↗</span></a></p>
          </div>
        </div>
      </footer>
    </>
  );
}
