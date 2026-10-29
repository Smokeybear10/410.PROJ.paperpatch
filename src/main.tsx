import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import DocumentApp from "./DocumentApp";
import TranscriptionApp from "./TranscriptionApp";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const transcriptionMode = params.get("mode") === "handwriting" || params.has("transcription");
const workbookMode =
  params.get("mode") === "workbooks" ||
  (params.has("session") && !params.has("document"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {transcriptionMode ? <TranscriptionApp /> : workbookMode ? <App /> : <DocumentApp />}
  </React.StrictMode>,
);
