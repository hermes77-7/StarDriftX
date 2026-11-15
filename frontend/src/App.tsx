import { useEffect, useRef, useState } from "react";
import { Landing } from "./Landing";
import { messages } from "./messages";
import type { Language, Messages } from "./messages";
import { Viewer } from "./viewer/Viewer";
import { isViewerLocation, readView, viewerHref } from "./viewer/viewState";
import "./App.css";

type Theme = "light" | "dark";

const exploreHref = "/explore?source=live&ra=150.14&dec=2.34&fov=0.12&band=4&epoch=4";

function readLocation() {
  return { pathname: window.location.pathname, search: window.location.search };
}

function readPreference(key: string) {
  try {
    return localStorage.getItem(`stardrift-${key}`);
  } catch {
    return null;
  }
}

function savePreference(key: string, value: string) {
  try {
    localStorage.setItem(`stardrift-${key}`, value);
  } catch {
    // Preferences still work for this session when storage is unavailable.
  }
}

function ShareButton({ t, shareUrl }: { t: Messages; shareUrl: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const link = useRef<HTMLInputElement>(null);
  const [feedback, setFeedback] = useState<"copied" | "copyError" | null>(null);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setFeedback("copied");
    } catch {
      link.current?.select();
      setFeedback("copyError");
    }
  }

  return (
    <>
      <button
        className="primary"
        onClick={() => {
          setFeedback(null);
          dialog.current?.showModal();
        }}
      >
        {t.share}
      </button>
      <dialog
        ref={dialog}
        className="share-dialog"
        aria-labelledby="share-title"
      >
        <div className="dialog-heading">
          <h2 id="share-title">{t.shareTitle}</h2>
          <form method="dialog">
            <button aria-label={t.close}>×</button>
          </form>
        </div>
        <p className="share-description">{t.shareDescription}</p>
        <label className="field">
          <span>{t.shareUrl}</span>
          <input
            ref={link}
            readOnly
            value={shareUrl}
            onFocus={(event) => event.currentTarget.select()}
          />
        </label>
        <button className="primary" onClick={copyLink}>
          {t.copy}
        </button>
        {feedback && <p role="status">{t[feedback]}</p>}
      </dialog>
    </>
  );
}

export default function App() {
  const [language, setLanguage] = useState<Language>(() =>
    readPreference("language") === "fr" ? "fr" : "en",
  );
  const [theme, setTheme] = useState<Theme>(() =>
    readPreference("theme") === "light" ? "light" : "dark",
  );
  const [{ pathname, search }, setLocation] = useState(readLocation);
  const t = messages[language];
  const isViewer = isViewerLocation(pathname, search);
  const isAbout = /^\/about\/?$/.test(pathname);
  const view = isViewer ? readView(search) : null;
  const shareUrl =
    view && (view.available || view.live)
      ? new URL(
          viewerHref(view.display, view.zoom, view.sample, view.pan, view.live, view.difference,
            view.bundledBand),
          window.location.origin,
        ).href
      : null;

  useEffect(() => {
    document.documentElement.lang = language;
    const pageTitle = isViewer ? t.explorer : isAbout ? t.about : null;
    document.title = pageTitle ? `${pageTitle} · StarDrift` : "StarDrift";
    savePreference("language", language);
  }, [isAbout, isViewer, language, t]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    savePreference("theme", theme);
  }, [theme]);

  useEffect(() => {
    const onPopState = () => setLocation(readLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function updateView(href: string) {
    window.history.replaceState(null, "", href);
    setLocation(readLocation());
  }

  return (
    <div className="app">
      <header className={isViewer ? "topbar" : "topbar landing-topbar"}>
        <a href="/" className="brand" aria-label={`StarDrift · ${t.home}`}>
          StarDrift
        </a>
        {isViewer ? (
          <span className="header-context">{t.explorer}</span>
        ) : (
          <nav className="landing-nav" aria-label={t.siteNavigation}>
            <a href="/" aria-current={pathname === "/" ? "page" : undefined}>{t.home}</a>
            <a href="/about" aria-current={isAbout ? "page" : undefined}>{t.about}</a>
            <a href={exploreHref}>{t.explore}</a>
          </nav>
        )}
        <div className="header-actions">
          <button
            className="preference"
            aria-label={t.language}
            onClick={() => setLanguage(language === "en" ? "fr" : "en")}
          >
            {language === "en" ? "FR" : "EN"}
          </button>
          <button
            className="preference theme-button"
            aria-label={theme === "dark" ? t.light : t.dark}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
          </button>
          {shareUrl && <ShareButton t={t} shareUrl={shareUrl} />}
        </div>
      </header>
      {view ? (
        <Viewer
          t={t}
          language={language}
          view={view}
          onSearchChange={updateView}
        />
      ) : (
        <Landing t={t} isAbout={isAbout} exploreHref={exploreHref} />
      )}
    </div>
  );
}
