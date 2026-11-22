#!/usr/bin/env python3
"""Moteur de rendu Markdown sécurisé anti-XSS pour l'onglet Audits (100 % stdlib)."""

MARKDOWN_VIEW_JS = r"""
  function renderSafeInline(escapedText) {
    let s = String(escapedText || "");
    const codeTokens = [];
    s = s.replace(/`([^`\n]+)`/g, (_, code) => {
      const idx = codeTokens.length;
      codeTokens.push("<code>" + code + "</code>");
      return "\u0000CODE" + idx + "\u0000";
    });

    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
      const cleanHref = String(href || "").replace(/&amp;/g, "&");
      const isLocalMd = /\.md(?:#[^#\s]*)?$/i.test(cleanHref) &&
        cleanHref.indexOf("..") === -1 &&
        cleanHref.indexOf(":") === -1 &&
        !cleanHref.startsWith("//");
      if (isLocalMd) {
        const refPath = cleanHref.split("#")[0].replace(/^\.\//, "");
        return '<button type="button" class="md-audit-link" data-action="open-audit-ref" data-ref="' + esc(refPath) + '">' + label + '</button>';
      }
      return '<span class="md-link" title="' + esc(cleanHref) + '">' + label + '</span>';
    });

    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");

    s = s.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codeTokens[Number(idx)] || "");
    return s;
  }

  function splitTableRow(line) {
    let trimmed = line.trim();
    if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
    if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
    return trimmed.split("|").map((c) => c.trim());
  }

  function isTableSep(line) {
    const cells = splitTableRow(line);
    return cells.length >= 1 && cells.every((c) => /^:?-{2,}:?$/.test(c));
  }

  function renderSafeMarkdown(rawMd) {
    const text = String(rawMd || "").replace(/\r\n?/g, "\n");
    if (!text.trim()) return '<div class="empty">Document Markdown vide.</div>';

    const lines = text.split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        i++;
        continue;
      }

      if (/^```/.test(trimmed)) {
        const lang = esc(trimmed.slice(3).trim());
        const codeLines = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) {
          codeLines.push(esc(lines[i]));
          i++;
        }
        if (i < lines.length) i++;
        out.push('<pre><code' + (lang ? ' data-lang="' + lang + '"' : '') + '>' + codeLines.join("\n") + '</code></pre>');
        continue;
      }

      const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (hMatch) {
        const lvl = hMatch[1].length;
        out.push('<h' + lvl + '>' + renderSafeInline(esc(hMatch[2])) + '</h' + lvl + '>');
        i++;
        continue;
      }

      if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        out.push("<hr />");
        i++;
        continue;
      }

      if (trimmed.startsWith("&gt;") || trimmed.startsWith(">")) {
        const qLines = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) {
          qLines.push(renderSafeInline(esc(lines[i].replace(/^\s*>\s?/, ""))));
          i++;
        }
        out.push("<blockquote>" + qLines.join("<br />") + "</blockquote>");
        continue;
      }

      if (trimmed.indexOf("|") >= 0 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const headers = splitTableRow(lines[i]);
        i += 2;
        const bodyRows = [];
        while (i < lines.length && lines[i].trim().indexOf("|") >= 0 && lines[i].trim() !== "") {
          bodyRows.push(splitTableRow(lines[i]));
          i++;
        }
        let tbl = '<div class="table-wrap"><table><thead><tr>';
        headers.forEach((h) => {
          tbl += "<th>" + renderSafeInline(esc(h)) + "</th>";
        });
        tbl += "</tr></thead><tbody>";
        bodyRows.forEach((row) => {
          tbl += "<tr>";
          for (let c = 0; c < headers.length; c++) {
            tbl += "<td>" + renderSafeInline(esc(row[c] || "")) + "</td>";
          }
          tbl += "</tr>";
        });
        tbl += "</tbody></table></div>";
        out.push(tbl);
        continue;
      }

      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          const itemTxt = lines[i].replace(/^\s*[-*+]\s+/, "");
          items.push("<li>" + renderSafeInline(esc(itemTxt)) + "</li>");
          i++;
        }
        out.push("<ul>" + items.join("") + "</ul>");
        continue;
      }

      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          const itemTxt = lines[i].replace(/^\s*\d+\.\s+/, "");
          items.push("<li>" + renderSafeInline(esc(itemTxt)) + "</li>");
          i++;
        }
        out.push("<ol>" + items.join("") + "</ol>");
        continue;
      }

      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^```/.test(lines[i].trim()) &&
        !/^#{1,4}\s+/.test(lines[i].trim()) &&
        !/^(?:-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim()) &&
        !/^\s*>/.test(lines[i]) &&
        !/^\s*[-*+]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i])
      ) {
        if (lines[i].indexOf("|") >= 0 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
          break;
        }
        paraLines.push(renderSafeInline(esc(lines[i].trim())));
        i++;
      }
      if (paraLines.length) {
        out.push("<p>" + paraLines.join(" ") + "</p>");
      } else {
        i++;
      }
    }

    return '<div class="md-doc">' + out.join("\n") + "</div>";
  }
"""
