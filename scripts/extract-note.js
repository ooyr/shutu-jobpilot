// 浏览器上下文脚本：抽取小红书笔记正文、元数据与评论区
// 由 collect.mjs 以 base64 编码传入，在页面上下文执行
(() => {
  const MAX_COMMENTS = 120;

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const textOf = (el) => (el && el.textContent ? clean(el.textContent) : "");
  const attrOf = (el, name) => (el && el.getAttribute ? el.getAttribute(name) : null);

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

  const pickText = (root, selectors) => {
    for (let i = 0; i < selectors.length; i += 1) {
      const nodes = root.querySelectorAll(selectors[i]);
      for (let j = 0; j < nodes.length; j += 1) {
        const value = textOf(nodes[j]);
        if (value.length > 0) return value;
      }
    }
    return "";
  };

  const metaContent = (property) => {
    const el = document.querySelector(
      'meta[property="' + property + '"], meta[name="' + property + '"]'
    );
    return el ? attrOf(el, "content") : null;
  };

  const noteRoot =
    document.querySelector("#noteContainer, [class*='note-container'], [class*='noteContainer']") ||
    document.body;

  const title =
    pickText(noteRoot, ["#detail-title", "[class*='note-title']", "h1", ".title"]) ||
    metaContent("og:title") ||
    document.title ||
    "";

  const desc = pickText(noteRoot, [
    "#detail-desc",
    "[class*='note-text']",
    "[class*='desc']"
  ]);

  const author = pickText(noteRoot, [
    "[class*='author'] [class*='name']",
    "[class*='author-wrapper'] [class*='name']",
    "[class*='username']",
    ".author .name"
  ]).slice(0, 60);

  const likeText = pickText(noteRoot, [
    "[class*='like'] [class*='count']",
    ".like-wrapper .count",
    "[class*='like-wrapper']"
  ]);
  const collectText = pickText(noteRoot, [
    "[class*='collect'] [class*='count']",
    ".collect-wrapper .count"
  ]);

  const commentNodes = Array.from(
    document.querySelectorAll("[class*='comment-item'], [class*='commentItem'], .comment-item")
  );

  const comments = [];
  const seen = Object.create(null);

  for (let i = 0; i < commentNodes.length && comments.length < MAX_COMMENTS; i += 1) {
    const node = commentNodes[i];
    const cid = attrOf(node, "data-comment-id") || attrOf(node, "id") || null;

    let body = "";
    const bodyEl = node.querySelector("[class*='content'], .content, [class*='note-text']");
    if (bodyEl) body = textOf(bodyEl);
    if (!body) body = textOf(node);
    if (!body) continue;

    const authorEl = node.querySelector(
      "[class*='author'] [class*='name'], [class*='name'], .author"
    );
    const likeEl = node.querySelector(
      "[class*='like'] [class*='count'], [class*='like'], .count"
    );
    const timeEl = node.querySelector("[class*='date'], [class*='time'], time");
    const cls = String(node.className || "");
    const isReply = cls.indexOf("reply") >= 0 || cls.indexOf("sub") >= 0;

    const key = cid
      ? "id:" + cid
      : "tx:" + textOf(authorEl) + "|" + body.slice(0, 60);
    if (seen[key]) continue;
    seen[key] = true;

    comments.push({
      cid: cid,
      author: textOf(authorEl).slice(0, 60),
      content: body.slice(0, 1000),
      likes: numOf(textOf(likeEl)),
      time_text: textOf(timeEl) || null,
      is_reply: isReply
    });
  }

  const pageText = document.body ? document.body.innerText : "";
  const captcha = /滑动验证|安全验证|请完成验证|操作频繁|访问异常/.test(pageText);
  const loginWall = /扫码登录|登录后查看|请先登录|手机号登录/.test(pageText);
  const expandHint = /展开|全文/.test(pageText);

  return {
    page: "note",
    note_id: idFromUrl(location.href),
    url: location.href,
    title: title.slice(0, 200),
    desc: desc.slice(0, 8000),
    author: author,
    published_text:
      pickText(noteRoot, ["[class*='date']", "[class*='time']", "time"]) || null,
    likes: numOf(likeText),
    collects: numOf(collectText),
    comments_count: commentNodes.length,
    comments: comments,
    diagnostics: {
      comments_loaded: comments.length > 0,
      comments_partial: commentNodes.length > comments.length,
      expand_hint: expandHint,
      blocked: captcha ? "captcha" : null,
      login_wall: loginWall
    }
  };
})()