// Apply the saved theme before the page draws (its own file: the page allows no inline scripts).
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "graphite"; } catch (e) {}
