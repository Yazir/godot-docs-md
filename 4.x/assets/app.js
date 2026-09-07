(function () {
	"use strict";

	var ROOT = window.SITE_ROOT || "./";
	var treeEl = document.getElementById("tree");
	var searchEl = document.getElementById("search");
	var resultsEl = document.getElementById("results");
	var searchData = null;
	var searchLoaded = false;
	var timer = null;

	function el(tag, cls, text) {
		var n = document.createElement(tag);
		if (cls) n.className = cls;
		if (text !== undefined) n.textContent = text;
		return n;
	}

	function pageUrl(p) {
		return ROOT + "pages/" + p.replace(/\.md$/, ".html");
	}

	// narrow-screen menu button
	var btn = el("button", null, "\u2630");
	btn.id = "menu-btn";
	btn.setAttribute("aria-label", "Toggle navigation");
	btn.addEventListener("click", function () {
		document.body.classList.toggle("nav-open");
	});
	document.body.appendChild(btn);

	document.addEventListener("click", function (e) {
		if (!resultsEl.hidden && !resultsEl.contains(e.target) && e.target !== searchEl) {
			resultsEl.hidden = true;
		}
	});

	fetch(ROOT + "index.json")
		.then(function (r) { return r.json(); })
		.then(function (data) {
			var shas = document.getElementById("meta-shas");
			if (shas) {
				shas.textContent = "godot-docs@" + String(data.docs_sha || "").slice(0, 8);
			}
			buildTree(data.docs || []);
		});

	function details(label, count, open) {
		var d = document.createElement("details");
		if (open) d.open = true;
		var s = document.createElement("summary");
		s.appendChild(el("span", null, label));
		if (count !== null && count !== undefined) {
			s.appendChild(el("span", "group-count", " " + count));
		}
		d.appendChild(s);
		return d;
	}

	function docItem(d) {
		var li = el("li");
		var a = document.createElement("a");
		a.href = pageUrl(d.p);
		a.textContent = d.t;
		a.dataset.path = d.p;
		li.appendChild(a);
		return li;
	}

	function prettyLabel(s) {
		return s.replace(/_/g, " ");
	}

	function countAll(node) {
		var n = node.docs.length;
		for (var k in node.subs) n += countAll(node.subs[k]);
		return n;
	}

	function buildTree(docs) {
		var classes = docs.filter(function (d) { return d.p.indexOf("classes/") === 0; });
		var manual = docs.filter(function (d) { return d.p.indexOf("manual/") === 0; });

		var clsDet = details("Classes", classes.length, false);
		var cul = el("ul");
		classes.forEach(function (d) { cul.appendChild(docItem(d)); });
		clsDet.appendChild(cul);
		treeEl.appendChild(clsDet);

		var root = { subs: {}, docs: [] };
		manual.forEach(function (d) {
			var parts = (d.c || "manual").split("/").filter(Boolean).slice(1);
			var node = root;
			parts.forEach(function (part) {
				if (!node.subs[part]) node.subs[part] = { subs: {}, docs: [] };
				node = node.subs[part];
			});
			node.docs.push(d);
		});

		var manDet = details("Manual", manual.length, true);
		manDet.appendChild(renderSubtree(root));
		treeEl.appendChild(manDet);

		highlightCurrent();
	}

	function renderSubtree(node) {
		var frag = document.createDocumentFragment();
		if (node.docs.length) {
			var ul = el("ul");
			node.docs.forEach(function (d) { ul.appendChild(docItem(d)); });
			frag.appendChild(ul);
		}
		Object.keys(node.subs).sort().forEach(function (key) {
			var child = node.subs[key];
			var det = details(prettyLabel(key), countAll(child), true);
			det.dataset.group = key;
			det.appendChild(renderSubtree(child));
			frag.appendChild(det);
		});
		return frag;
	}

	function highlightCurrent() {
		var links = treeEl.querySelectorAll("a[data-path]");
		for (var i = 0; i < links.length; i++) {
			var p = links[i].getAttribute("data-path").replace(/\.md$/, ".html");
			if (location.pathname.indexOf("/pages/" + p) !== -1) {
				links[i].classList.add("current");
				var det = links[i].closest("details");
				while (det) {
					det.open = true;
					det = det.parentElement ? det.parentElement.closest("details") : null;
				}
				links[i].scrollIntoView({ block: "center" });
			}
		}
	}

	// ---------------- search ----------------

	function escHtml(s) {
		return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
	}

	function ensureSearch() {
		if (searchLoaded) return Promise.resolve();
		return fetch(ROOT + "search.json")
			.then(function (r) { return r.json(); })
			.then(function (data) {
				searchData = data;
				searchLoaded = true;
			});
	}

	function snippetHtml(x, idx, qlen) {
		var start = Math.max(0, idx - 60);
		var end = Math.min(x.length, idx + qlen + 80);
		var pre = start > 0 ? "\u2026" : "";
		var post = end < x.length ? "\u2026" : "";
		return pre + escHtml(x.slice(start, idx)) +
			"<mark>" + escHtml(x.slice(idx, idx + qlen)) + "</mark>" +
			escHtml(x.slice(idx + qlen, end)) + post;
	}

	function runSearch(q) {
		var qlc = q.toLowerCase();
		if (qlc.length < 2) {
			resultsEl.hidden = true;
			resultsEl.innerHTML = "";
			return;
		}
		ensureSearch().then(function () {
			var hits = [];
			for (var i = 0; i < searchData.length; i++) {
				var d = searchData[i];
				var idx = d.x.indexOf(qlc);
				if (idx === -1) continue;
				hits.push({ d: d, idx: idx, title: d.t.toLowerCase().indexOf(qlc) !== -1 });
				if (hits.length > 500) break;
			}
			hits.sort(function (a, b) {
				return (b.title - a.title) || (a.idx - b.idx);
			});
			var out = [];
			hits.slice(0, 30).forEach(function (h) {
				out.push(
					'<a class="r" href="' + pageUrl(h.d.p).replace(/"/g, "&quot;") + '">' +
					"<div><strong>" + escHtml(h.d.t) + "</strong></div>" +
					'<div class="rp">' + escHtml(h.d.p) + "</div>" +
					"<div>" + snippetHtml(h.d.x, h.idx, qlc.length) + "</div></a>"
				);
			});
			resultsEl.innerHTML = out.join("") ||
				'<div class="r"><div class="rp">No results</div></div>';
			resultsEl.hidden = false;
		});
	}

	function filterTree(q) {
		var qlc = q.toLowerCase();
		var links = treeEl.querySelectorAll("a[data-path]");
		for (var i = 0; i < links.length; i++) {
			var li = links[i].parentElement;
			var match = qlc === "" || links[i].textContent.toLowerCase().indexOf(qlc) !== -1;
			li.classList.toggle("no-match", !match);
		}
		var groups = treeEl.querySelectorAll("details[data-group]");
		for (var j = 0; j < groups.length; j++) {
			var has = groups[j].querySelector("li:not(.no-match)");
			groups[j].classList.toggle("no-match", !has);
		}
		treeEl.classList.toggle("hidden-empty", qlc !== "");
	}

	searchEl.addEventListener("input", function () {
		var q = searchEl.value.trim();
		clearTimeout(timer);
		timer = setTimeout(function () { runSearch(q); }, 180);
		filterTree(q);
	});

	searchEl.addEventListener("keydown", function (e) {
		if (e.key === "Enter") {
			var first = resultsEl.querySelector("a.r");
			if (first && !resultsEl.hidden) location.href = first.getAttribute("href");
		}
		if (e.key === "Escape") {
			resultsEl.hidden = true;
			filterTree("");
		}
	});
})();
