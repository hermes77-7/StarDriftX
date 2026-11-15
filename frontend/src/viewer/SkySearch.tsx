import { useEffect, useRef, useState } from "react";
import type { Messages } from "../messages";
import type { ResolveResponse } from "../types/api";
import { parseSkySearch } from "./search";
import type { Coordinates } from "./search";
import { createRequests, fetchJson, HttpError } from "./requests";

const resolvedNames = createRequests<ResolveResponse>(32, () => 300000);

export function SkySearch({ t, onPosition }: {
  t: Messages;
  onPosition: (position: Coordinates) => void;
}) {
  const [query, setQuery] = useState("");
  const [feedback, setFeedback] = useState<"searchInvalid" | "objectUnknown" | "resolverUnavailable" | "resolving">();
  const active = useRef<AbortController>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function submit() {
    active.current?.abort();
    const parsed = parseSkySearch(query);
    if ("invalid" in parsed) {
      setFeedback("searchInvalid");
      return;
    }
    if ("position" in parsed) {
      setFeedback(undefined);
      onPosition(parsed.position);
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    setFeedback("resolving");
    try {
      const params = new URLSearchParams({ name: parsed.name });
      const object = await resolvedNames(parsed.name, controller.signal, (shared) =>
        fetchJson<ResolveResponse>("/api/resolve?" + params, shared),
      );
      if (controller.signal.aborted) return;
      setQuery(object.name);
      setFeedback(undefined);
      onPosition({ ra: object.ra, dec: object.dec });
    } catch (error) {
      if (!controller.signal.aborted) {
        setFeedback(error instanceof HttpError && error.status === 404 ? "objectUnknown" : "resolverUnavailable");
      }
    }
  }

  return (
    <form className="sky-search" role="search" onSubmit={(event) => {
      event.preventDefault();
      void submit();
    }}>
      <label className="field">
        <span>{t.searchSky}</span>
        <input value={query} placeholder="M31" onChange={(event) => {
          active.current?.abort();
          setFeedback(undefined);
          setQuery(event.target.value);
        }} />
      </label>
      <button type="submit">{t.searchAction}</button>
      <p className="search-example">{t.searchExample}</p>
      {feedback && <p role={feedback === "resolving" ? "status" : "alert"}>{t[feedback]}</p>}
    </form>
  );
}
