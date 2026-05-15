import { defineUxlConfig } from "@damsleth/ux-loop"

/**
 * UX loop config for the cognitive-ledger web UI (Phase 1 + Phase 2 search).
 *
 * The dev server is the Python `ledger web` command, exposed via the
 * `dev` npm script so playwright can spin it up. We pin known stems
 * from the real corpus for the note-detail flows; if those notes get
 * renamed or deleted these flows will need updating.
 */
export default defineUxlConfig({
  capture: {
    runner: "playwright",
    baseUrl: process.env.UI_REVIEW_BASE_URL || "http://127.0.0.1:5173",
    timeoutMs: 120000,
    onboarding: { status: "complete" },

    flowInventory: [
      { id: "recent", label: "Browse - recent activity", path: "/browse", required: true },
      { id: "browse-all", label: "Browse - all notes", path: "/browse/all", required: true },
      { id: "browse-facts", label: "Browse - facts listing", path: "/browse/facts", required: true },
      { id: "browse-loops", label: "Browse - loops (all)", path: "/browse/loops", required: true },
      { id: "browse-loops-open", label: "Browse - loops filtered to open", path: "/browse/loops?status=open", required: true },
      { id: "note-identity", label: "Note - identity (rich, many wikilinks)", path: "/note/id__personal_profile", required: true },
      { id: "note-fact", label: "Note - simple fact", path: "/note/fact__norconsult_account", required: true },
      { id: "note-loop", label: "Note - open loop", path: "/note/loop__forutsigbar_dagsplanlegging", required: true },
      { id: "note-preference", label: "Note - preference", path: "/note/pref__efficiency_over_sycophancy", required: false },
      { id: "search-empty", label: "Search - empty state", path: "/search", required: true },
      { id: "search-results", label: "Search - results page", path: "/search?q=nocos", required: true },
      { id: "search-semantic", label: "Search - semantic mode", path: "/search?q=hjelpekorps&mode=semantic", required: false },
      { id: "search-no-results", label: "Search - no matches", path: "/search?q=qqqq+xxxx+vvvv", required: true },
    ],

    flowMapping: {
      recent: ["recent"],
      "browse-all": ["browse-all"],
      "browse-facts": ["browse-facts"],
      "browse-loops": ["browse-loops"],
      "browse-loops-open": ["browse-loops-open"],
      "note-identity": ["note-identity"],
      "note-fact": ["note-fact"],
      "note-loop": ["note-loop"],
      "note-preference": ["note-preference"],
      "search-empty": ["search-empty"],
      "search-results": ["search-results"],
      "search-semantic": ["search-semantic"],
      "search-no-results": ["search-no-results"],
    },

    playwright: {
      startCommand: "dev",
      devices: [
        { name: "desktop", width: 1280, height: 900 },
        { name: "wide", width: 1680, height: 1050 },
        { name: "mobile", width: 390, height: 844 },
      ],
      flows: [
        {
          label: "Browse - recent",
          name: "recent",
          path: "/browse",
          waitFor: "main",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Browse - all",
          name: "browse-all",
          path: "/browse/all",
          waitFor: "main",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Browse - facts",
          name: "browse-facts",
          path: "/browse/facts",
          waitFor: "main",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Browse - loops",
          name: "browse-loops",
          path: "/browse/loops",
          waitFor: "main",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Browse - loops open",
          name: "browse-loops-open",
          path: "/browse/loops?status=open",
          waitFor: "main",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Note - identity",
          name: "note-identity",
          path: "/note/id__personal_profile",
          waitFor: "article.note",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Note - fact",
          name: "note-fact",
          path: "/note/fact__norconsult_account",
          waitFor: "article.note",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Note - loop",
          name: "note-loop",
          path: "/note/loop__forutsigbar_dagsplanlegging",
          waitFor: "article.note",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Note - preference",
          name: "note-preference",
          path: "/note/pref__efficiency_over_sycophancy",
          waitFor: "article.note",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Search - empty state",
          name: "search-empty",
          path: "/search",
          waitFor: "#search-results-area",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
        {
          label: "Search - results page",
          name: "search-results",
          path: "/search?q=nocos",
          waitFor: ".search-results, .empty",
          settleMs: 400,
          screenshot: { fullPage: true },
        },
        {
          label: "Search - semantic mode",
          name: "search-semantic",
          path: "/search?q=hjelpekorps&mode=semantic",
          waitFor: ".search-results, .empty, .search-summary",
          settleMs: 800,
          screenshot: { fullPage: true },
        },
        {
          label: "Search - no matches",
          name: "search-no-results",
          path: "/search?q=qqqq+xxxx+vvvv",
          waitFor: ".empty, .search-summary",
          settleMs: 200,
          screenshot: { fullPage: true },
        },
      ],
    },
  },

  review: {
    runner: "codex",
  },

  implement: {
    target: "current",
  },
})
