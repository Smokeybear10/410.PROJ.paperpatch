import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import DocumentApp from "./DocumentApp";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const workbookMode =
  params.get("mode") === "workbooks" ||
  (params.has("session") && !params.has("document"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {workbookMode ? <App /> : <DocumentApp />}
  </React.StrictMode>,
);
