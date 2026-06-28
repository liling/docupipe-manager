(function () {
  "use strict";
  // Safe DOM construction helpers — no innerHTML, so user data can never
  // be parsed as HTML. All text is inserted via text/createTextNode.

  // el(tag, attrs?, ...children) -> Element
  //   attrs.text   -> textContent (never parsed as HTML)
  //   attrs.class  -> className
  //   attrs.dataset-> object.assign into dataset
  //   attrs.onX    -> addEventListener("x", fn)
  //   other keys   -> setAttribute
  //   children: strings/numbers become text nodes; null/false skipped.
  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) applyAttrs(node, attrs);
    if (arguments.length > 2) appendKids(node, Array.prototype.slice.call(arguments, 2));
    return node;
  }

  function applyAttrs(node, attrs) {
    for (var key in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, key)) continue;
      var v = attrs[key];
      if (v == null || v === false) continue;
      if (key === "class") { node.className = v; }
      else if (key === "text") { node.textContent = v; }
      else if (key === "dataset") { Object.assign(node.dataset, v); }
      else if (key.slice(0, 2) === "on" && typeof v === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), v);
      } else {
        node.setAttribute(key, v === true ? "" : String(v));
      }
    }
  }

  function appendKids(node, kids) {
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k == null || k === false || k === true) continue;
      if (Array.isArray(k)) { appendKids(node, k); continue; }
      node.appendChild(typeof k === "object" ? k : document.createTextNode(String(k)));
    }
  }

  // fill(container, ...kids) — clear then append. Safe replacement for innerHTML.
  function fill(container) {
    container.replaceChildren();
    if (arguments.length > 1) appendKids(container, Array.prototype.slice.call(arguments, 1));
    return container;
  }

  function clear(container) { container.replaceChildren(); return container; }

  window.DP = { el: el, fill: fill, clear: clear };
})();
