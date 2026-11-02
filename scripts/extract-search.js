// 浏览器上下文脚本：抽取小红书搜索结果卡片
// 由 collect.mjs 以 base64 编码传入，在页面上下文执行
(() => {
  const MAX_ITEMS = 80;

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const textOf = (el) => (el && el.textContent ? clean(el.textContent) : "");
  const attrOf = (el, name) => (el && el.getAttribute ? el.getAttribute(name) : null);

  const absUrl = (href) => {
    if (!href) return null;
    try {
      return new URL(href, location.href).href;
    } catch (err) {
      return null;
    }
  };

  const idFromUrl = (url) => {
    if (!url) return null;
    const match = url.match(/\/(?:explore|discovery\/item)\/([0-9a-zA-Z]+)/);
    return match ? match[1] : null;
  };

  const numOf = (raw) => {
    if (!raw) return null;
    const text = String(raw).replace(/\s+/g, "").replace(/,/g, "");
    const match = text.match(/^([0-9]+(?:\.[0-9]+)?)([万亿wWkK]?)/);
    if (!match) return null;
    const base = parseFloat(match[1]);
    if (Number.isNaN(base)) return null;
    const unit = match[2].toLowerCase();
    if (unit === "万" || unit === "w") return Math.round(base * 10000);
    if (unit === "亿") return Math.round(base * 100000000);
    if (unit === "千" || unit === "k") return Math.round(base * 1000);
    return Math.round(base);
  };

  const pick = (root, selectors) => {
    for (let i = 0; i < selectors.length; i += 1) {
      const nodes = root.querySelectorAll(selectors[i]);
      for (let j = 0; j < nodes.length; j += 1) {
        if (textOf(nodes[j]).length > 0) return nodes[j];
      }
    }
    return null;
  };

  const containerOf = (anchor) => {
    let node = anchor;
    for (let depth = 0; depth < 8; depth += 1) {
      if (!node.parentElement) break;
      node = node.parentElement;
      const cls = String(node.className || "");
      if (cls.indexOf("note-item") >= 0 || cls.indexOf("noteItem") >= 0) return node;
      if (cls.indexOf("cover") >= 0 && cls.indexOf("footer") >= 0) return node;
    }
    return anchor.parentElement || anchor;
  };

  const anchors = Array.from(
    document.querySelectorAll('a[href*="/explore/"], a[href*="/discovery/item/"]')
  );

  const seen = Object.create(null);
  const items = [];

  for (let i = 0; i < anchors.length && items.length < MAX_ITEMS; i += 1) {
    const anchor = anchors[i];
    const url = absUrl(attrOf(anchor, "href"));
    const noteId = idFromUrl(url);
    if (!noteId || seen[noteId]) continue;
    seen[noteId] = true;

    const box = containerOf(anchor);
    const titleEl = pick(box, ["[class*='title']", "h3", ".title"]);
    let title = textOf(titleEl) || attrOf(anchor, "title") || "";
    if (!title) {
      const lines = String(box.innerText || "")
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      title = lines.length > 0 ? lines[0] : "";
    }

    const authorEl = pick(box, [
      "[class*='author'] [class*='name']",
      "[class*='author']",
      ".name"
    ]);
    const likeEl = pick(box, [
      "[class*='like'] [class*='count']",
      "[class*='like']",
      ".count"
    ]);
    const timeEl = pick(box, ["[class*='time']", "time"]);

    items.push({
      note_id: noteId,
      url: url,
      title: title.slice(0, 200),
      author: textOf(authorEl).slice(0, 60),
      published_text: textOf(timeEl) || null,
      likes: numOf(textOf(likeEl)),
      cover: attrOf(box.querySelector("img"), "src") || null
    });
  }

  const bodyText = document.body ? document.body.innerText : "";
  const captcha = /滑动验证|安全验证|请完成验证|操作频繁|访问异常/.test(bodyText);
  const loginWall = /扫码登录|登录后查看|请先登录|手机号登录/.test(bodyText);

  return {
    page: "search",
    url: location.href,
    doc_title: document.title,
    items: items,
    diagnostics: {
      anchors: anchors.length,
      cards_found: items.length,
      blocked: captcha ? "captcha" : null,
      login_wall: loginWall,
      empty_page: items.length === 0
    }
  };
})()