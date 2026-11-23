#!/usr/bin/env python3
"""Moteur de rendu Markdown sécurisé anti-XSS pour l'onglet Audits Agents (100 % stdlib)."""

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
        cleanHref.indexOf("://") === -1 &&
        !cleanHref.startsWith("//");
      if (isLocalMd) {
        const refPath = cleanHref.split("#")[0].replace(/^\.\//, "");
        return '<button type="button" class="md-audit-link" data-action="open-audit-ref" data-ref="' + esc(refPath) + '">' + label + '</button>';
      }
      if (cleanHref.indexOf("://") >= 0) {
        return '<span class="md-ext-link" title="' + esc(cleanHref) + '">' + label + ' ↗</span>';
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

  function isQuoteLine(line) {
    return /^\s*(?:>|&gt;(?:\s|$))/.test(String(line || ""));
  }

  function stripQuotePrefix(line) {
    return String(line || "").replace(/^\s*(?:>|&gt;)\s?/, "");
  }

  function parseListBlock(lines, startIdx) {
    const items = [];
    let i = startIdx;
    const firstM = lines[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
    if (!firstM) return { html: "", nextIdx: i + 1 };
    const baseIndent = firstM[1].length;
    const ordered = /\d+\./.test(firstM[2]);
    const tag = ordered ? "ol" : "ul";

    while (i < lines.length) {
      const m = lines[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
      if (!m) break;
      const indent = m[1].length;
      const itemOrdered = /\d+\./.test(m[2]);
      if (indent < baseIndent || (indent === baseIndent && itemOrdered !== ordered)) {
        break;
      }
      if (indent > baseIndent) {
        const nested = parseListBlock(lines, i);
        if (items.length) {
          items[items.length - 1] = items[items.length - 1].replace(/<\/li>$/, nested.html + "</li>");
        } else {
          items.push("<li>" + nested.html + "</li>");
        }
        i = nested.nextIdx;
        continue;
      }
      items.push("<li>" + renderSafeInline(esc(m[3])) + "</li>");
      i++;
    }
    return { html: "<" + tag + ">" + items.join("") + "</" + tag + ">", nextIdx: i };
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

      const hMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
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

      if (isQuoteLine(line)) {
        const rawQuoteLines = [];
        while (i < lines.length && isQuoteLine(lines[i])) {
          rawQuoteLines.push(stripQuotePrefix(lines[i]));
          i++;
        }
        if (!rawQuoteLines.length) {
          i++;
          continue;
        }
        const calloutMatch = rawQuoteLines[0].trim().match(/^\[!(NOTE|WARNING|TIP|CAUTION|IMPORTANT)\]\s*(.*)$/i);
        if (calloutMatch) {
          const cType = calloutMatch[1].toLowerCase();
          const restFirst = calloutMatch[2] ? [calloutMatch[2]] : [];
          const bodyLines = restFirst.concat(rawQuoteLines.slice(1)).map((ln) => renderSafeInline(esc(ln)));
          out.push(
            '<div class="md-callout md-callout-' + esc(cType) + '">' +
            '<div class="md-callout-title">' + esc(cType) + '</div>' +
            (bodyLines.length ? '<div class="md-callout-body">' + bodyLines.join("<br />") + '</div>' : '') +
            '</div>'
          );
        } else {
          const qRendered = rawQuoteLines.map((ln) => renderSafeInline(esc(ln)));
          out.push("<blockquote>" + qRendered.join("<br />") + "</blockquote>");
        }
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

      if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
        const listRes = parseListBlock(lines, i);
        out.push(listRes.html);
        i = listRes.nextIdx;
        continue;
      }

      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^```/.test(lines[i].trim()) &&
        !/^#{1,6}\s+/.test(lines[i].trim()) &&
        !/^(?:-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim()) &&
        !isQuoteLine(lines[i]) &&
        !/^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])
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
