// Applies the saved theme before first paint (external file: the CSP forbids inline scripts).
try {
  var t = JSON.parse(localStorage.getItem("assurance.theme") || "null");
  if (!t) t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
} catch (e) { /* storage unavailable */ }
