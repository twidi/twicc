//#region \0rolldown/runtime.js
var e = Object.create, t = Object.defineProperty, n = Object.getOwnPropertyDescriptor, r = Object.getOwnPropertyNames, i = Object.getPrototypeOf, a = Object.prototype.hasOwnProperty, o = (e, t, n) => () => {
	if (n) throw n[0];
	try {
		return e && (t = e(e = 0)), t;
	} catch (e) {
		throw n = [e], e;
	}
}, s = (e, t) => () => (t || (e((t = { exports: {} }).exports, t), e = null), t.exports), c = (e, n) => {
	let r = {};
	for (var i in e) t(r, i, {
		get: e[i],
		enumerable: !0
	});
	return n || t(r, Symbol.toStringTag, { value: "Module" }), r;
}, l = (e, i, o, s) => {
	if (i && typeof i == "object" || typeof i == "function") for (var c = r(i), l = 0, u = c.length, d; l < u; l++) d = c[l], !a.call(e, d) && d !== o && t(e, d, {
		get: ((e) => i[e]).bind(null, d),
		enumerable: !(s = n(i, d)) || s.enumerable
	});
	return e;
}, u = (n, r, o) => (o = n == null ? {} : e(i(n)), l(r || !n || !n.__esModule || !a.call(n, "default") ? t(o, "default", {
	value: n,
	enumerable: !0
}) : o, n));
//#endregion
//#region node_modules/@intlify/shared/dist/shared.mjs
function d(e, t) {
	typeof console < "u" && (console.warn("[intlify] " + e), t && console.warn(t.stack));
}
var f = typeof window < "u", p = (e, t = !1) => t ? Symbol.for(e) : Symbol(e), m = (e, t, n) => h({
	l: e,
	k: t,
	s: n
}), h = (e) => JSON.stringify(e).replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029").replace(/\u0027/g, "\\u0027"), g = (e) => typeof e == "number" && isFinite(e), _ = (e) => re(e) === "[object Date]", v = (e) => re(e) === "[object RegExp]", y = (e) => P(e) && Object.keys(e).length === 0, b = Object.assign, x = Object.create, S = (e = null) => x(e), C, w = () => C || (C = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : S());
function T(e) {
	return e.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;").replace(/\//g, "&#x2F;").replace(/=/g, "&#x3D;");
}
function E(e) {
	return e.replace(/&(?![a-zA-Z0-9#]{2,6};)/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&apos;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function D(e) {
	return e = e.replace(/(\w+)\s*=\s*"([^"]*)"/g, (e, t, n) => `${t}="${E(n)}"`), e = e.replace(/(\w+)\s*=\s*'([^']*)'/g, (e, t, n) => `${t}='${E(n)}'`), /\s*on\w+\s*=\s*["']?[^"'>]+["']?/gi.test(e) && (e = e.replace(/(\s+)(on)(\w+\s*=)/gi, "$1&#111;n$3")), [/(\s+(?:href|src|action|formaction)\s*=\s*["']?)\s*javascript:/gi, /(style\s*=\s*["'][^"']*url\s*\(\s*)javascript:/gi].forEach((t) => {
		e = e.replace(t, "$1javascript&#58;");
	}), e;
}
var ee = Object.prototype.hasOwnProperty;
function O(e, t) {
	return ee.call(e, t);
}
var k = Array.isArray, A = (e) => typeof e == "function", j = (e) => typeof e == "string", M = (e) => typeof e == "boolean", N = (e) => typeof e == "object" && !!e, te = (e) => N(e) && A(e.then) && A(e.catch), ne = Object.prototype.toString, re = (e) => ne.call(e), P = (e) => re(e) === "[object Object]", ie = (e) => e == null ? "" : k(e) || P(e) && e.toString === ne ? JSON.stringify(e, null, 2) : String(e);
function ae(e, t = "") {
	return e.reduce((e, n, r) => r === 0 ? e + n : e + t + n, "");
}
function oe() {
	let e = /* @__PURE__ */ new Map();
	return {
		events: e,
		on(t, n) {
			let r = e.get(t);
			r && r.push(n) || e.set(t, [n]);
		},
		off(t, n) {
			let r = e.get(t);
			r && r.splice(r.indexOf(n) >>> 0, 1);
		},
		emit(t, n) {
			(e.get(t) || []).slice().map((e) => e(n)), (e.get("*") || []).slice().map((e) => e(t, n));
		}
	};
}
var se = (e) => !N(e) || k(e);
function ce(e, t) {
	if (se(e) || se(t)) throw Error("Invalid value");
	let n = [{
		src: e,
		des: t
	}];
	for (; n.length;) {
		let { src: e, des: t } = n.pop();
		Object.keys(e).forEach((r) => {
			r !== "__proto__" && (N(e[r]) && !N(t[r]) && (t[r] = Array.isArray(e[r]) ? [] : S()), se(t[r]) || se(e[r]) ? t[r] = e[r] : n.push({
				src: e[r],
				des: t[r]
			}));
		});
	}
}
//#endregion
//#region node_modules/@intlify/message-compiler/dist/message-compiler.mjs
function le(e, t, n) {
	return {
		line: e,
		column: t,
		offset: n
	};
}
function ue(e, t, n) {
	let r = {
		start: e,
		end: t
	};
	return n != null && (r.source = n), r;
}
var F = {
	EXPECTED_TOKEN: 1,
	INVALID_TOKEN_IN_PLACEHOLDER: 2,
	UNTERMINATED_SINGLE_QUOTE_IN_PLACEHOLDER: 3,
	UNKNOWN_ESCAPE_SEQUENCE: 4,
	INVALID_UNICODE_ESCAPE_SEQUENCE: 5,
	UNBALANCED_CLOSING_BRACE: 6,
	UNTERMINATED_CLOSING_BRACE: 7,
	EMPTY_PLACEHOLDER: 8,
	NOT_ALLOW_NEST_PLACEHOLDER: 9,
	INVALID_LINKED_FORMAT: 10,
	MUST_HAVE_MESSAGES_IN_PLURAL: 11,
	UNEXPECTED_EMPTY_LINKED_MODIFIER: 12,
	UNEXPECTED_EMPTY_LINKED_KEY: 13,
	UNEXPECTED_LEXICAL_ANALYSIS: 14,
	UNHANDLED_CODEGEN_NODE_TYPE: 15,
	UNHANDLED_MINIFIER_NODE_TYPE: 16
};
F.EXPECTED_TOKEN, F.INVALID_TOKEN_IN_PLACEHOLDER, F.UNTERMINATED_SINGLE_QUOTE_IN_PLACEHOLDER, F.UNKNOWN_ESCAPE_SEQUENCE, F.INVALID_UNICODE_ESCAPE_SEQUENCE, F.UNBALANCED_CLOSING_BRACE, F.UNTERMINATED_CLOSING_BRACE, F.EMPTY_PLACEHOLDER, F.NOT_ALLOW_NEST_PLACEHOLDER, F.INVALID_LINKED_FORMAT, F.MUST_HAVE_MESSAGES_IN_PLURAL, F.UNEXPECTED_EMPTY_LINKED_MODIFIER, F.UNEXPECTED_EMPTY_LINKED_KEY, F.UNEXPECTED_LEXICAL_ANALYSIS, F.UNHANDLED_CODEGEN_NODE_TYPE, F.UNHANDLED_MINIFIER_NODE_TYPE;
function de(e, t, n = {}) {
	let { domain: r, messages: i, args: a } = n, o = SyntaxError(String(e));
	return o.code = e, t && (o.location = t), o.domain = r, o;
}
function fe(e) {
	throw e;
}
var pe = " ", me = "\r", he = "\n", ge = "\u2028", _e = "\u2029";
function ve(e) {
	let t = e, n = 0, r = 1, i = 1, a = 0, o = (e) => t[e] === me && t[e + 1] === he, s = (e) => t[e] === he, c = (e) => t[e] === _e, l = (e) => t[e] === ge, u = (e) => o(e) || s(e) || c(e) || l(e), d = () => n, f = () => r, p = () => i, m = () => a, h = (e) => o(e) || c(e) || l(e) ? he : t[e], g = () => h(n), _ = () => h(n + a);
	function v() {
		return a = 0, u(n) && (r++, i = 0), o(n) && n++, n++, i++, t[n];
	}
	function y() {
		return o(n + a) && a++, a++, t[n + a];
	}
	function b() {
		n = 0, r = 1, i = 1, a = 0;
	}
	function x(e = 0) {
		a = e;
	}
	function S() {
		let e = n + a;
		for (; e !== n;) v();
		a = 0;
	}
	return {
		index: d,
		line: f,
		column: p,
		peekOffset: m,
		charAt: h,
		currentChar: g,
		currentPeek: _,
		next: v,
		peek: y,
		reset: b,
		resetPeek: x,
		skipToPeek: S
	};
}
var ye = void 0, be = "'", xe = "tokenizer";
function Se(e, t = {}) {
	let n = t.location !== !1, r = ve(e), i = () => r.index(), a = () => le(r.line(), r.column(), r.index()), o = a(), s = i(), c = {
		currentType: 13,
		offset: s,
		startLoc: o,
		endLoc: o,
		lastType: 13,
		lastOffset: s,
		lastStartLoc: o,
		lastEndLoc: o,
		braceNest: 0,
		inLinked: !1,
		text: ""
	}, l = () => c, { onError: u } = t;
	function d(e, t, r, ...i) {
		let a = l();
		if (t.column += r, t.offset += r, u) {
			let r = de(e, n ? ue(a.startLoc, t) : null, {
				domain: xe,
				args: i
			});
			u(r);
		}
	}
	function f(e, t, r) {
		e.endLoc = a(), e.currentType = t;
		let i = { type: t };
		return n && (i.loc = ue(e.startLoc, e.endLoc)), r != null && (i.value = r), i;
	}
	let p = (e) => f(e, 13);
	function m(e, t) {
		return e.currentChar() === t ? (e.next(), t) : (d(F.EXPECTED_TOKEN, a(), 0, t), "");
	}
	function h(e) {
		let t = "";
		for (; e.currentPeek() === pe || e.currentPeek() === he;) t += e.currentPeek(), e.peek();
		return t;
	}
	function g(e) {
		let t = h(e);
		return e.skipToPeek(), t;
	}
	function _(e) {
		if (e === ye) return !1;
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t === 95;
	}
	function v(e) {
		if (e === ye) return !1;
		let t = e.charCodeAt(0);
		return t >= 48 && t <= 57;
	}
	function y(e, t) {
		let { currentType: n } = t;
		if (n !== 2) return !1;
		h(e);
		let r = _(e.currentPeek());
		return e.resetPeek(), r;
	}
	function b(e, t) {
		let { currentType: n } = t;
		if (n !== 2) return !1;
		h(e);
		let r = v(e.currentPeek() === "-" ? e.peek() : e.currentPeek());
		return e.resetPeek(), r;
	}
	function x(e, t) {
		let { currentType: n } = t;
		if (n !== 2) return !1;
		h(e);
		let r = e.currentPeek() === be;
		return e.resetPeek(), r;
	}
	function S(e, t) {
		let { currentType: n } = t;
		if (n !== 7) return !1;
		h(e);
		let r = e.currentPeek() === ".";
		return e.resetPeek(), r;
	}
	function C(e, t) {
		let { currentType: n } = t;
		if (n !== 8) return !1;
		h(e);
		let r = _(e.currentPeek());
		return e.resetPeek(), r;
	}
	function w(e, t) {
		let { currentType: n } = t;
		if (n !== 7 && n !== 11) return !1;
		h(e);
		let r = e.currentPeek() === ":";
		return e.resetPeek(), r;
	}
	function T(e, t) {
		let { currentType: n } = t;
		if (n !== 9) return !1;
		let r = () => {
			let t = e.currentPeek();
			return t === "{" ? _(e.peek()) : t === "@" || t === "|" || t === ":" || t === "." || t === pe || !t ? !1 : t === he ? (e.peek(), r()) : D(e, !1);
		}, i = r();
		return e.resetPeek(), i;
	}
	function E(e) {
		h(e);
		let t = e.currentPeek() === "|";
		return e.resetPeek(), t;
	}
	function D(e, t = !0) {
		let n = (t = !1, r = "") => {
			let i = e.currentPeek();
			return i === "{" || i === "@" || !i ? t : i === "|" ? r !== pe && r !== he : i === pe ? (e.peek(), n(!0, pe)) : i !== he || (e.peek(), n(!0, he));
		}, r = n();
		return t && e.resetPeek(), r;
	}
	function ee(e, t) {
		let n = e.currentChar();
		if (n !== ye) return t(n) ? (e.next(), n) : null;
	}
	function O(e) {
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t >= 48 && t <= 57 || t === 95 || t === 36;
	}
	function k(e) {
		return ee(e, O);
	}
	function A(e) {
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t >= 48 && t <= 57 || t === 95 || t === 36 || t === 45;
	}
	function j(e) {
		return ee(e, A);
	}
	function M(e) {
		let t = e.charCodeAt(0);
		return t >= 48 && t <= 57;
	}
	function N(e) {
		return ee(e, M);
	}
	function te(e) {
		let t = e.charCodeAt(0);
		return t >= 48 && t <= 57 || t >= 65 && t <= 70 || t >= 97 && t <= 102;
	}
	function ne(e) {
		return ee(e, te);
	}
	function re(e) {
		let t = "", n = "";
		for (; t = N(e);) n += t;
		return n;
	}
	function P(e) {
		let t = "";
		for (;;) {
			let n = e.currentChar();
			if (n === "\\") {
				let r = e.peek();
				r === "{" || r === "}" || r === "@" || r === "|" || r === "\\" ? (t += n + r, e.next(), e.next()) : (e.resetPeek(), t += n, e.next());
			} else if (n === "{" || n === "}" || n === "@" || n === "|" || !n) break;
			else if (n === pe || n === he) {
				if (D(e)) t += n, e.next();
				else if (E(e)) break;
				else t += n, e.next();
			} else t += n, e.next();
		}
		return t;
	}
	function ie(e) {
		g(e);
		let t = "", n = "";
		for (; t = j(e);) n += t;
		let r = e.currentChar();
		if (r && r !== "}" && r !== ye && r !== pe && r !== he && r !== "　") {
			let t = ge(e);
			return d(F.INVALID_TOKEN_IN_PLACEHOLDER, a(), 0, n + t), n + t;
		}
		return e.currentChar() === ye && d(F.UNTERMINATED_CLOSING_BRACE, a(), 0), n;
	}
	function ae(e) {
		g(e);
		let t = "";
		return e.currentChar() === "-" ? (e.next(), t += `-${re(e)}`) : t += re(e), e.currentChar() === ye && d(F.UNTERMINATED_CLOSING_BRACE, a(), 0), t;
	}
	function oe(e) {
		return e !== be && e !== he;
	}
	function se(e) {
		g(e), m(e, "'");
		let t = "", n = "";
		for (; t = ee(e, oe);) n += t === "\\" ? ce(e) : t;
		let r = e.currentChar();
		return r === he || r === ye ? (d(F.UNTERMINATED_SINGLE_QUOTE_IN_PLACEHOLDER, a(), 0), r === he && (e.next(), m(e, "'")), n) : (m(e, "'"), n);
	}
	function ce(e) {
		let t = e.currentChar();
		switch (t) {
			case "\\":
			case "'": return e.next(), `\\${t}`;
			case "u": return fe(e, t, 4);
			case "U": return fe(e, t, 6);
			default: return d(F.UNKNOWN_ESCAPE_SEQUENCE, a(), 0, t), "";
		}
	}
	function fe(e, t, n) {
		m(e, t);
		let r = "";
		for (let i = 0; i < n; i++) {
			let n = ne(e);
			if (!n) {
				d(F.INVALID_UNICODE_ESCAPE_SEQUENCE, a(), 0, `\\${t}${r}${e.currentChar()}`);
				break;
			}
			r += n;
		}
		return `\\${t}${r}`;
	}
	function me(e) {
		return e !== "{" && e !== "}" && e !== pe && e !== he;
	}
	function ge(e) {
		g(e);
		let t = "", n = "";
		for (; t = ee(e, me);) n += t;
		return n;
	}
	function _e(e) {
		let t = "", n = "";
		for (; t = k(e);) n += t;
		return n;
	}
	function Se(e) {
		let t = (n) => {
			let r = e.currentChar();
			return r === "{" || r === "@" || r === "|" || r === "(" || r === ")" || !r || r === pe ? n : (n += r, e.next(), t(n));
		};
		return t("");
	}
	function Ce(e) {
		g(e);
		let t = m(e, "|");
		return g(e), t;
	}
	function we(e, t) {
		let n = null;
		switch (e.currentChar()) {
			case "{": return t.braceNest >= 1 && d(F.NOT_ALLOW_NEST_PLACEHOLDER, a(), 0), e.next(), n = f(t, 2, "{"), g(e), t.braceNest++, n;
			case "}": return t.braceNest > 0 && t.currentType === 2 && d(F.EMPTY_PLACEHOLDER, a(), 0), e.next(), n = f(t, 3, "}"), t.braceNest--, t.braceNest > 0 && g(e), t.inLinked && t.braceNest === 0 && (t.inLinked = !1), n;
			case "@": return t.braceNest > 0 && d(F.UNTERMINATED_CLOSING_BRACE, a(), 0), n = Te(e, t) || p(t), t.braceNest = 0, n;
			default: {
				let r = !0, i = !0, o = !0;
				if (E(e)) return t.braceNest > 0 && d(F.UNTERMINATED_CLOSING_BRACE, a(), 0), n = f(t, 1, Ce(e)), t.braceNest = 0, t.inLinked = !1, n;
				if (t.braceNest > 0 && (t.currentType === 4 || t.currentType === 5 || t.currentType === 6)) return d(F.UNTERMINATED_CLOSING_BRACE, a(), 0), t.braceNest = 0, Ee(e, t);
				if (r = y(e, t)) return n = f(t, 4, ie(e)), g(e), n;
				if (i = b(e, t)) return n = f(t, 5, ae(e)), g(e), n;
				if (o = x(e, t)) return n = f(t, 6, se(e)), g(e), n;
				if (!r && !i && !o) return n = f(t, 12, ge(e)), d(F.INVALID_TOKEN_IN_PLACEHOLDER, a(), 0, n.value), g(e), n;
				break;
			}
		}
		return n;
	}
	function Te(e, t) {
		let { currentType: n } = t, r = null, i = e.currentChar();
		switch ((n === 7 || n === 8 || n === 11 || n === 9) && (i === he || i === pe) && d(F.INVALID_LINKED_FORMAT, a(), 0), i) {
			case "@": return e.next(), r = f(t, 7, "@"), t.inLinked = !0, r;
			case ".": return g(e), e.next(), f(t, 8, ".");
			case ":": return g(e), e.next(), f(t, 9, ":");
			default: return E(e) ? (r = f(t, 1, Ce(e)), t.braceNest = 0, t.inLinked = !1, r) : S(e, t) || w(e, t) ? (g(e), Te(e, t)) : C(e, t) ? (g(e), f(t, 11, _e(e))) : T(e, t) ? (g(e), i === "{" ? we(e, t) || r : f(t, 10, Se(e))) : (n === 7 && d(F.INVALID_LINKED_FORMAT, a(), 0), t.braceNest = 0, t.inLinked = !1, Ee(e, t));
		}
	}
	function Ee(e, t) {
		let n = { type: 13 };
		if (t.braceNest > 0) return we(e, t) || p(t);
		if (t.inLinked) return Te(e, t) || p(t);
		switch (e.currentChar()) {
			case "{": return we(e, t) || p(t);
			case "}": return d(F.UNBALANCED_CLOSING_BRACE, a(), 0), e.next(), f(t, 3, "}");
			case "@": return Te(e, t) || p(t);
			default:
				if (E(e)) return n = f(t, 1, Ce(e)), t.braceNest = 0, t.inLinked = !1, n;
				if (D(e)) return f(t, 0, P(e));
		}
		return n;
	}
	function De() {
		let { currentType: e, offset: t, startLoc: n, endLoc: o } = c;
		return c.lastType = e, c.lastOffset = t, c.lastStartLoc = n, c.lastEndLoc = o, c.offset = i(), c.startLoc = a(), r.currentChar() === ye ? f(c, 13) : Ee(r, c);
	}
	return {
		nextToken: De,
		currentOffset: i,
		currentPosition: a,
		context: l
	};
}
var Ce = "parser", we = /(?:\\\\|\\'|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{6}))/g, Te = /\\([\\@{}|])/g;
function Ee(e, t) {
	return t;
}
function De(e, t, n) {
	switch (e) {
		case "\\\\": return "\\";
		case "\\'": return "'";
		default: {
			let e = parseInt(t || n, 16);
			return e <= 55295 || e >= 57344 ? String.fromCodePoint(e) : "�";
		}
	}
}
function Oe(e = {}) {
	let t = e.location !== !1, { onError: n } = e;
	function r(e, r, i, a, ...o) {
		let s = e.currentPosition();
		if (s.offset += a, s.column += a, n) {
			let e = de(r, t ? ue(i, s) : null, {
				domain: Ce,
				args: o
			});
			n(e);
		}
	}
	function i(e, n, r) {
		let i = { type: e };
		return t && (i.start = n, i.end = n, i.loc = {
			start: r,
			end: r
		}), i;
	}
	function a(e, n, r, i) {
		t && (e.end = n, e.loc && (e.loc.end = r));
	}
	function o(e, t) {
		let n = e.context(), r = i(3, n.offset, n.startLoc);
		return r.value = t.replace(Te, Ee), a(r, e.currentOffset(), e.currentPosition()), r;
	}
	function s(e, t) {
		let { lastOffset: n, lastStartLoc: r } = e.context(), o = i(5, n, r);
		return o.index = parseInt(t, 10), e.nextToken(), a(o, e.currentOffset(), e.currentPosition()), o;
	}
	function c(e, t) {
		let { lastOffset: n, lastStartLoc: r } = e.context(), o = i(4, n, r);
		return o.key = t, e.nextToken(), a(o, e.currentOffset(), e.currentPosition()), o;
	}
	function l(e, t) {
		let { lastOffset: n, lastStartLoc: r } = e.context(), o = i(9, n, r);
		return o.value = t.replace(we, De), e.nextToken(), a(o, e.currentOffset(), e.currentPosition()), o;
	}
	function u(e) {
		let t = e.nextToken(), n = e.context(), { lastOffset: o, lastStartLoc: s } = n, c = i(8, o, s);
		return t.type === 11 ? (t.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, n.lastStartLoc, 0, ke(t)), c.value = t.value || "", a(c, e.currentOffset(), e.currentPosition()), { node: c }) : (r(e, F.UNEXPECTED_EMPTY_LINKED_MODIFIER, n.lastStartLoc, 0), c.value = "", a(c, o, s), {
			nextConsumeToken: t,
			node: c
		});
	}
	function d(e, t) {
		let n = e.context(), r = i(7, n.offset, n.startLoc);
		return r.value = t, a(r, e.currentOffset(), e.currentPosition()), r;
	}
	function f(e) {
		let t = e.context(), n = i(6, t.offset, t.startLoc), o = e.nextToken();
		if (o.type === 8) {
			let t = u(e);
			n.modifier = t.node, o = t.nextConsumeToken || e.nextToken();
		}
		switch (o.type !== 9 && r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(o)), o = e.nextToken(), o.type === 2 && (o = e.nextToken()), o.type) {
			case 10:
				o.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(o)), n.key = d(e, o.value || "");
				break;
			case 4:
				o.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(o)), n.key = c(e, o.value || "");
				break;
			case 5:
				o.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(o)), n.key = s(e, o.value || "");
				break;
			case 6:
				o.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(o)), n.key = l(e, o.value || "");
				break;
			default: {
				r(e, F.UNEXPECTED_EMPTY_LINKED_KEY, t.lastStartLoc, 0);
				let s = e.context(), c = i(7, s.offset, s.startLoc);
				return c.value = "", a(c, s.offset, s.startLoc), n.key = c, a(n, s.offset, s.startLoc), {
					nextConsumeToken: o,
					node: n
				};
			}
		}
		return a(n, e.currentOffset(), e.currentPosition()), { node: n };
	}
	function p(e) {
		let t = e.context(), n = i(2, t.currentType === 1 ? e.currentOffset() : t.offset, t.currentType === 1 ? t.endLoc : t.startLoc);
		n.items = [];
		let u = null;
		do {
			let i = u || e.nextToken();
			switch (u = null, i.type) {
				case 0:
					i.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(i)), n.items.push(o(e, i.value || ""));
					break;
				case 5:
					i.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(i)), n.items.push(s(e, i.value || ""));
					break;
				case 4:
					i.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(i)), n.items.push(c(e, i.value || ""));
					break;
				case 6:
					i.value ?? r(e, F.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, ke(i)), n.items.push(l(e, i.value || ""));
					break;
				case 7: {
					let t = f(e);
					n.items.push(t.node), u = t.nextConsumeToken || null;
					break;
				}
			}
		} while (t.currentType !== 13 && t.currentType !== 1);
		return a(n, t.currentType === 1 ? t.lastOffset : e.currentOffset(), t.currentType === 1 ? t.lastEndLoc : e.currentPosition()), n;
	}
	function m(e, t, n, o) {
		let s = e.context(), c = o.items.length === 0, l = i(1, t, n);
		l.cases = [], l.cases.push(o);
		do {
			let t = p(e);
			c || (c = t.items.length === 0), l.cases.push(t);
		} while (s.currentType !== 13);
		return c && r(e, F.MUST_HAVE_MESSAGES_IN_PLURAL, n, 0), a(l, e.currentOffset(), e.currentPosition()), l;
	}
	function h(e) {
		let t = e.context(), { offset: n, startLoc: r } = t, i = p(e);
		return t.currentType === 13 ? i : m(e, n, r, i);
	}
	function g(n) {
		let o = Se(n, b({}, e)), s = o.context(), c = i(0, s.offset, s.startLoc);
		return t && c.loc && (c.loc.source = n), c.body = h(o), e.onCacheKey && (c.cacheKey = e.onCacheKey(n)), s.currentType !== 13 && r(o, F.UNEXPECTED_LEXICAL_ANALYSIS, s.lastStartLoc, 0, n[s.offset] || ""), a(c, o.currentOffset(), o.currentPosition()), c;
	}
	return { parse: g };
}
function ke(e) {
	if (e.type === 13) return "EOF";
	let t = (e.value || "").replace(/\r?\n/gu, "\\n");
	return t.length > 10 ? t.slice(0, 9) + "…" : t;
}
function Ae(e, t = {}) {
	let n = {
		ast: e,
		helpers: /* @__PURE__ */ new Set()
	};
	return {
		context: () => n,
		helper: (e) => (n.helpers.add(e), e)
	};
}
function je(e, t) {
	for (let n = 0; n < e.length; n++) Me(e[n], t);
}
function Me(e, t) {
	switch (e.type) {
		case 1:
			je(e.cases, t), t.helper("plural");
			break;
		case 2:
			je(e.items, t);
			break;
		case 6:
			Me(e.key, t), t.helper("linked"), t.helper("type");
			break;
		case 5:
			t.helper("interpolate"), t.helper("list");
			break;
		case 4: t.helper("interpolate"), t.helper("named");
	}
}
function I(e, t = {}) {
	let n = Ae(e);
	n.helper("normalize"), e.body && Me(e.body, n);
	let r = n.context();
	e.helpers = Array.from(r.helpers);
}
function Ne(e) {
	let t = e.body;
	return t.type === 2 ? Pe(t) : t.cases.forEach((e) => Pe(e)), e;
}
function Pe(e) {
	if (e.items.length === 1) {
		let t = e.items[0];
		(t.type === 3 || t.type === 9) && (e.static = t.value, delete t.value);
	} else {
		let t = [];
		for (let n = 0; n < e.items.length; n++) {
			let r = e.items[n];
			if (r.type !== 3 && r.type !== 9 || r.value == null) break;
			t.push(r.value);
		}
		if (t.length === e.items.length) {
			e.static = ae(t);
			for (let t = 0; t < e.items.length; t++) {
				let n = e.items[t];
				(n.type === 3 || n.type === 9) && delete n.value;
			}
		}
	}
}
function Fe(e) {
	switch (e.t = e.type, e.type) {
		case 0: {
			let t = e;
			Fe(t.body), t.b = t.body, delete t.body;
			break;
		}
		case 1: {
			let t = e, n = t.cases;
			for (let e = 0; e < n.length; e++) Fe(n[e]);
			t.c = n, delete t.cases;
			break;
		}
		case 2: {
			let t = e, n = t.items;
			for (let e = 0; e < n.length; e++) Fe(n[e]);
			t.i = n, delete t.items, t.static && (t.s = t.static, delete t.static);
			break;
		}
		case 3:
		case 9:
		case 8:
		case 7: {
			let t = e;
			t.value && (t.v = t.value, delete t.value);
			break;
		}
		case 6: {
			let t = e;
			Fe(t.key), t.k = t.key, delete t.key, t.modifier && (Fe(t.modifier), t.m = t.modifier, delete t.modifier);
			break;
		}
		case 5: {
			let t = e;
			t.i = t.index, delete t.index;
			break;
		}
		case 4: {
			let t = e;
			t.k = t.key, delete t.key;
			break;
		}
	}
	delete e.type;
}
function Ie(e, t) {
	let { sourceMap: n, filename: r, breakLineCode: i, needIndent: a } = t, o = t.location !== !1, s = {
		filename: r,
		code: "",
		column: 1,
		line: 1,
		offset: 0,
		map: void 0,
		breakLineCode: i,
		needIndent: a,
		indentLevel: 0
	};
	o && e.loc && (s.source = e.loc.source);
	let c = () => s;
	function l(e, t) {
		s.code += e;
	}
	function u(e, t = !0) {
		let n = t ? i : "";
		l(a ? n + "  ".repeat(e) : n);
	}
	function d(e = !0) {
		let t = ++s.indentLevel;
		e && u(t);
	}
	function f(e = !0) {
		let t = --s.indentLevel;
		e && u(t);
	}
	function p() {
		u(s.indentLevel);
	}
	return {
		context: c,
		push: l,
		indent: d,
		deindent: f,
		newline: p,
		helper: (e) => `_${e}`,
		needIndent: () => s.needIndent
	};
}
function Le(e, t) {
	let { helper: n } = e;
	e.push(`${n("linked")}(`), Ve(e, t.key), t.modifier ? (e.push(", "), Ve(e, t.modifier), e.push(", _type")) : e.push(", undefined, _type"), e.push(")");
}
function Re(e, t) {
	let { helper: n, needIndent: r } = e;
	e.push(`${n("normalize")}([`), e.indent(r());
	let i = t.items.length;
	for (let n = 0; n < i && (Ve(e, t.items[n]), n !== i - 1); n++) e.push(", ");
	e.deindent(r()), e.push("])");
}
function ze(e, t) {
	let { helper: n, needIndent: r } = e;
	if (t.cases.length > 1) {
		e.push(`${n("plural")}([`), e.indent(r());
		let i = t.cases.length;
		for (let n = 0; n < i && (Ve(e, t.cases[n]), n !== i - 1); n++) e.push(", ");
		e.deindent(r()), e.push("])");
	}
}
function Be(e, t) {
	t.body ? Ve(e, t.body) : e.push("null");
}
function Ve(e, t) {
	let { helper: n } = e;
	switch (t.type) {
		case 0:
			Be(e, t);
			break;
		case 1:
			ze(e, t);
			break;
		case 2:
			Re(e, t);
			break;
		case 6:
			Le(e, t);
			break;
		case 8:
			e.push(JSON.stringify(t.value), t);
			break;
		case 7:
			e.push(JSON.stringify(t.value), t);
			break;
		case 5:
			e.push(`${n("interpolate")}(${n("list")}(${t.index}))`, t);
			break;
		case 4:
			e.push(`${n("interpolate")}(${n("named")}(${JSON.stringify(t.key)}))`, t);
			break;
		case 9:
			e.push(JSON.stringify(t.value), t);
			break;
		case 3: e.push(JSON.stringify(t.value), t);
	}
}
var He = (e, t = {}) => {
	let n = j(t.mode) ? t.mode : "normal", r = j(t.filename) ? t.filename : "message.intl", i = !!t.sourceMap, a = t.breakLineCode == null ? n === "arrow" ? ";" : "\n" : t.breakLineCode, o = t.needIndent ? t.needIndent : n !== "arrow", s = e.helpers || [], c = Ie(e, {
		mode: n,
		filename: r,
		sourceMap: i,
		breakLineCode: a,
		needIndent: o
	});
	c.push(n === "normal" ? "function __msg__ (ctx) {" : "(ctx) => {"), c.indent(o), s.length > 0 && (c.push(`const { ${ae(s.map((e) => `${e}: _${e}`), ", ")} } = ctx`), c.newline()), c.push("return "), Ve(c, e), c.deindent(o), c.push("}"), delete e.helpers;
	let { code: l, map: u } = c.context();
	return {
		ast: e,
		code: l,
		map: u ? u.toJSON() : void 0
	};
};
function Ue(e, t = {}) {
	let n = b({}, t), r = !!n.jit, i = !!n.minify, a = n.optimize == null || n.optimize, o = Oe(n).parse(e);
	return r ? (a && Ne(o), i && Fe(o), {
		ast: o,
		code: ""
	}) : (I(o, n), He(o, n));
}
//#endregion
//#region node_modules/@intlify/core-base/dist/core-base.mjs
function We() {
	typeof __INTLIFY_DROP_MESSAGE_COMPILER__ != "boolean" && (w().__INTLIFY_DROP_MESSAGE_COMPILER__ = !1);
}
function Ge(e) {
	return N(e) && tt(e) === 0 && (O(e, "b") || O(e, "body"));
}
var Ke = ["b", "body"];
function qe(e) {
	return ct(e, Ke);
}
var Je = ["c", "cases"];
function Ye(e) {
	return ct(e, Je, []);
}
var Xe = ["s", "static"];
function Ze(e) {
	return ct(e, Xe);
}
var Qe = ["i", "items"];
function $e(e) {
	return ct(e, Qe, []);
}
var et = ["t", "type"];
function tt(e) {
	return ct(e, et);
}
var nt = ["v", "value"];
function rt(e, t) {
	let n = ct(e, nt);
	if (n != null) return n;
	throw ut(t);
}
var it = ["m", "modifier"];
function at(e) {
	return ct(e, it);
}
var ot = ["k", "key"];
function st(e) {
	let t = ct(e, ot);
	if (t) return t;
	throw ut(6);
}
function ct(e, t, n) {
	for (let n = 0; n < t.length; n++) {
		let r = t[n];
		if (O(e, r) && e[r] != null) return e[r];
	}
	return n;
}
var lt = [
	...Ke,
	...Je,
	...Xe,
	...Qe,
	...ot,
	...it,
	...nt,
	...et
];
function ut(e) {
	return /* @__PURE__ */ Error(`unhandled node type: ${e}`);
}
function dt(e) {
	return (t) => ft(t, e);
}
function ft(e, t) {
	let n = qe(t);
	if (n == null) throw ut(0);
	if (tt(n) === 1) {
		let t = Ye(n);
		return e.plural(t.reduce((t, n) => [...t, pt(e, n)], []));
	}
	return pt(e, n);
}
function pt(e, t) {
	let n = Ze(t);
	if (n != null) return e.type === "text" ? n : e.normalize([n]);
	{
		let n = $e(t).reduce((t, n) => [...t, mt(e, n)], []);
		return e.normalize(n);
	}
}
function mt(e, t) {
	let n = tt(t);
	switch (n) {
		case 3: return rt(t, n);
		case 9: return rt(t, n);
		case 4: {
			let r = t;
			if (O(r, "k") && r.k) return e.interpolate(e.named(r.k));
			if (O(r, "key") && r.key) return e.interpolate(e.named(r.key));
			throw ut(n);
		}
		case 5: {
			let r = t;
			if (O(r, "i") && g(r.i)) return e.interpolate(e.list(r.i));
			if (O(r, "index") && g(r.index)) return e.interpolate(e.list(r.index));
			throw ut(n);
		}
		case 6: {
			let n = t, r = at(n), i = st(n);
			return e.linked(mt(e, i), r ? mt(e, r) : void 0, e.type);
		}
		case 7: return rt(t, n);
		case 8: return rt(t, n);
		default: throw Error(`unhandled node on format message part: ${n}`);
	}
}
var ht = (e) => e, gt = S();
function _t(e, t = {}) {
	let n = !1, r = t.onError || fe;
	return t.onError = (e) => {
		n = !0, r(e);
	}, {
		...Ue(e, t),
		detectError: n
	};
}
/* #__NO_SIDE_EFFECTS__ */
function vt(e, t) {
	if (!__INTLIFY_DROP_MESSAGE_COMPILER__ && j(e)) {
		M(t.warnHtmlMessage) && t.warnHtmlMessage;
		let n = (t.onCacheKey || ht)(e), r = gt[n];
		if (r) return r;
		let { ast: i, detectError: a } = _t(e, {
			...t,
			location: !1,
			jit: !0
		}), o = dt(i);
		return a ? o : gt[n] = o;
	}
	{
		let t = e.cacheKey;
		return t ? gt[t] || (gt[t] = dt(e)) : dt(e);
	}
}
var yt = {
	INVALID_ARGUMENT: 17,
	INVALID_DATE_ARGUMENT: 18,
	INVALID_ISO_DATE_ARGUMENT: 19,
	NOT_SUPPORT_NON_STRING_MESSAGE: 20,
	NOT_SUPPORT_LOCALE_PROMISE_VALUE: 21,
	NOT_SUPPORT_LOCALE_ASYNC_FUNCTION: 22,
	NOT_SUPPORT_LOCALE_TYPE: 23
};
function bt(e) {
	return de(e, null, void 0);
}
yt.INVALID_ARGUMENT, yt.INVALID_DATE_ARGUMENT, yt.INVALID_ISO_DATE_ARGUMENT, yt.NOT_SUPPORT_NON_STRING_MESSAGE, yt.NOT_SUPPORT_LOCALE_PROMISE_VALUE, yt.NOT_SUPPORT_LOCALE_ASYNC_FUNCTION, yt.NOT_SUPPORT_LOCALE_TYPE;
function xt(e, t) {
	return t.locale == null ? Ct(e.locale) : Ct(t.locale);
}
var St;
function Ct(e) {
	if (j(e)) return e;
	if (A(e)) {
		if (e.resolvedOnce && St != null) return St;
		if (e.constructor.name === "Function") {
			let t = e();
			if (te(t)) throw bt(yt.NOT_SUPPORT_LOCALE_PROMISE_VALUE);
			return St = t;
		}
		throw bt(yt.NOT_SUPPORT_LOCALE_ASYNC_FUNCTION);
	}
	throw bt(yt.NOT_SUPPORT_LOCALE_TYPE);
}
function wt(e, t, n) {
	return [.../* @__PURE__ */ new Set([n, ...k(t) ? t : N(t) ? Object.keys(t) : j(t) ? [t] : [n]])];
}
function Tt(e, t, n) {
	let r = j(n) ? n : Vt, i = e;
	i.__localeChainCache || (i.__localeChainCache = /* @__PURE__ */ new Map());
	let a = i.__localeChainCache.get(r);
	if (!a) {
		a = [];
		let e = [n];
		for (; k(e);) e = Et(a, e, t);
		let o = k(t) || !P(t) ? t : t.default ? t.default : null;
		e = j(o) ? [o] : o, k(e) && Et(a, e, !1), i.__localeChainCache.set(r, a);
	}
	return a;
}
function Et(e, t, n) {
	let r = !0;
	for (let i = 0; i < t.length && M(r); i++) {
		let a = t[i];
		j(a) && (r = Dt(e, t[i], n));
	}
	return r;
}
function Dt(e, t, n) {
	let r, i = t.split("-");
	do
		r = Ot(e, i.join("-"), n), i.splice(-1, 1);
	while (i.length && r === !0);
	return r;
}
function Ot(e, t, n) {
	let r = !1;
	if (!e.includes(t) && (r = !0, t)) {
		r = t[t.length - 1] !== "!";
		let i = t.replace(/!/g, "");
		e.push(i), (k(n) || P(n)) && n[i] && (r = n[i]);
	}
	return r;
}
var kt = [];
kt[0] = {
	w: [0],
	i: [3, 0],
	"[": [4],
	o: [7]
}, kt[1] = {
	w: [1],
	".": [2],
	"[": [4],
	o: [7]
}, kt[2] = {
	w: [2],
	i: [3, 0],
	0: [3, 0]
}, kt[3] = {
	i: [3, 0],
	0: [3, 0],
	w: [1, 1],
	".": [2, 1],
	"[": [4, 1],
	o: [7, 1]
}, kt[4] = {
	"'": [5, 0],
	"\"": [6, 0],
	"[": [4, 2],
	"]": [1, 3],
	o: 8,
	l: [4, 0]
}, kt[5] = {
	"'": [4, 0],
	o: 8,
	l: [5, 0]
}, kt[6] = {
	"\"": [4, 0],
	o: 8,
	l: [6, 0]
};
var At = /^\s?(?:true|false|-?[\d.]+|'[^']*'|"[^"]*")\s?$/;
function jt(e) {
	return At.test(e);
}
function Mt(e) {
	let t = e.charCodeAt(0);
	return t === e.charCodeAt(e.length - 1) && (t === 34 || t === 39) ? e.slice(1, -1) : e;
}
function Nt(e) {
	if (e == null) return "o";
	switch (e.charCodeAt(0)) {
		case 91:
		case 93:
		case 46:
		case 34:
		case 39: return e;
		case 95:
		case 36:
		case 45: return "i";
		case 9:
		case 10:
		case 13:
		case 160:
		case 65279:
		case 8232:
		case 8233: return "w";
	}
	return "i";
}
function Pt(e) {
	let t = e.trim();
	return e.charAt(0) === "0" && isNaN(parseInt(e)) ? !1 : jt(t) ? Mt(t) : "*" + t;
}
function Ft(e) {
	let t = [], n = -1, r = 0, i = 0, a, o, s, c, l, u, d, f = [];
	f[0] = () => {
		o === void 0 ? o = s : o += s;
	}, f[1] = () => {
		o !== void 0 && (t.push(o), o = void 0);
	}, f[2] = () => {
		f[0](), i++;
	}, f[3] = () => {
		if (i > 0) i--, r = 4, f[0]();
		else {
			if (i = 0, o === void 0 || (o = Pt(o), o === !1)) return !1;
			f[1]();
		}
	};
	function p() {
		let t = e[n + 1];
		if (r === 5 && t === "'" || r === 6 && t === "\"") return n++, s = "\\" + t, f[0](), !0;
	}
	for (; r !== null;) if (n++, a = e[n], !(a === "\\" && p())) {
		if (c = Nt(a), d = kt[r], l = d[c] || d.l || 8, l === 8 || (r = l[0], l[1] !== void 0 && (u = f[l[1]], u && (s = a, u() === !1)))) return;
		if (r === 7) return t;
	}
}
var It = /* @__PURE__ */ new Map();
function Lt(e, t) {
	return N(e) ? e[t] : null;
}
function Rt(e, t) {
	if (!N(e)) return null;
	let n = It.get(t);
	if (n || (n = Ft(t), n && It.set(t, n)), !n) return null;
	let r = n.length, i = e, a = 0;
	for (; a < r;) {
		let e = n[a];
		if (lt.includes(e) && Ge(i) || !N(i) || !O(i, e)) return null;
		let t = i[e];
		if (t === void 0 || A(i)) return null;
		i = t, a++;
	}
	return i;
}
var zt = {
	NOT_FOUND_KEY: 1,
	FALLBACK_TO_TRANSLATE: 2,
	CANNOT_FORMAT_NUMBER: 3,
	FALLBACK_TO_NUMBER_FORMAT: 4,
	CANNOT_FORMAT_DATE: 5,
	FALLBACK_TO_DATE_FORMAT: 6,
	EXPERIMENTAL_CUSTOM_MESSAGE_COMPILER: 7,
	INVALID_NUMBER_ARGUMENT: 8,
	INVALID_DATE_ARGUMENT: 9
};
zt.NOT_FOUND_KEY, zt.FALLBACK_TO_TRANSLATE, zt.CANNOT_FORMAT_NUMBER, zt.FALLBACK_TO_NUMBER_FORMAT, zt.CANNOT_FORMAT_DATE, zt.FALLBACK_TO_DATE_FORMAT, zt.EXPERIMENTAL_CUSTOM_MESSAGE_COMPILER, zt.INVALID_NUMBER_ARGUMENT, zt.INVALID_DATE_ARGUMENT;
var Bt = "11.3.2", Vt = "en-US", Ht = (e) => `${e.charAt(0).toLocaleUpperCase()}${e.substr(1)}`;
function Ut() {
	return {
		upper: (e, t) => t === "text" && j(e) ? e.toUpperCase() : t === "vnode" && N(e) && "__v_isVNode" in e ? e.children.toUpperCase() : e,
		lower: (e, t) => t === "text" && j(e) ? e.toLowerCase() : t === "vnode" && N(e) && "__v_isVNode" in e ? e.children.toLowerCase() : e,
		capitalize: (e, t) => t === "text" && j(e) ? Ht(e) : t === "vnode" && N(e) && "__v_isVNode" in e ? Ht(e.children) : e
	};
}
var Wt;
function Gt(e) {
	Wt = e;
}
var Kt;
function qt(e) {
	Kt = e;
}
var Jt;
function Yt(e) {
	Jt = e;
}
var Xt = null, Zt = (e) => {
	Xt = e;
}, Qt = () => Xt, $t = 0;
function en(e = {}) {
	let t = A(e.onWarn) ? e.onWarn : d, n = j(e.version) ? e.version : Bt, r = j(e.locale) || A(e.locale) ? e.locale : Vt, i = A(r) ? Vt : r, a = k(e.fallbackLocale) || P(e.fallbackLocale) || j(e.fallbackLocale) || e.fallbackLocale === !1 ? e.fallbackLocale : i, o = P(e.messages) ? e.messages : tn(i), s = P(e.datetimeFormats) ? e.datetimeFormats : tn(i), c = P(e.numberFormats) ? e.numberFormats : tn(i), l = b(S(), e.modifiers, Ut()), u = e.pluralRules || S(), f = A(e.missing) ? e.missing : null, p = M(e.missingWarn) || v(e.missingWarn) ? e.missingWarn : !0, m = M(e.fallbackWarn) || v(e.fallbackWarn) ? e.fallbackWarn : !0, h = !!e.fallbackFormat, g = !!e.unresolving, _ = A(e.postTranslation) ? e.postTranslation : null, y = P(e.processor) ? e.processor : null, x = !M(e.warnHtmlMessage) || e.warnHtmlMessage, C = !!e.escapeParameter, w = A(e.messageCompiler) ? e.messageCompiler : Wt, T = A(e.messageResolver) ? e.messageResolver : Kt || Lt, E = A(e.localeFallbacker) ? e.localeFallbacker : Jt || wt, D = N(e.fallbackContext) ? e.fallbackContext : void 0, ee = e, O = N(ee.__datetimeFormatters) ? ee.__datetimeFormatters : /* @__PURE__ */ new Map(), te = N(ee.__numberFormatters) ? ee.__numberFormatters : /* @__PURE__ */ new Map(), ne = N(ee.__meta) ? ee.__meta : {};
	$t++;
	let re = {
		version: n,
		cid: $t,
		locale: r,
		fallbackLocale: a,
		messages: o,
		modifiers: l,
		pluralRules: u,
		missing: f,
		missingWarn: p,
		fallbackWarn: m,
		fallbackFormat: h,
		unresolving: g,
		postTranslation: _,
		processor: y,
		warnHtmlMessage: x,
		escapeParameter: C,
		messageCompiler: w,
		messageResolver: T,
		localeFallbacker: E,
		fallbackContext: D,
		onWarn: t,
		__meta: ne
	};
	return re.datetimeFormats = s, re.numberFormats = c, re.__datetimeFormatters = O, re.__numberFormatters = te, re;
}
var tn = (e) => ({ [e]: S() });
function nn(e, t, n, r, i) {
	let { missing: a, onWarn: o } = e;
	if (a !== null) {
		let r = a(e, n, t, i);
		return j(r) ? r : t;
	}
	return t;
}
function rn(e, t, n) {
	let r = e;
	r.__localeChainCache = /* @__PURE__ */ new Map(), e.localeFallbacker(e, n, t);
}
function an(e, t) {
	return e !== t && e.split("-")[0] === t.split("-")[0];
}
function on(e, t) {
	let n = t.indexOf(e);
	if (n === -1) return !1;
	for (let r = n + 1; r < t.length; r++) if (an(e, t[r])) return !0;
	return !1;
}
var sn = typeof Intl < "u";
sn && Intl.DateTimeFormat, sn && Intl.NumberFormat;
function cn(e, ...t) {
	let { datetimeFormats: n, unresolving: r, fallbackLocale: i, onWarn: a, localeFallbacker: o } = e, { __datetimeFormatters: s } = e;
	if (!j(t[0]) && !_(t[0]) && !g(t[0])) return "";
	let [c, l, u, d] = un(...t), f = M(u.missingWarn) ? u.missingWarn : e.missingWarn;
	M(u.fallbackWarn) ? u.fallbackWarn : e.fallbackWarn;
	let p = !!u.part, m = xt(e, u), h = o(e, i, m);
	if (!j(c) || c === "") return new Intl.DateTimeFormat(m.replace(/!/g, ""), d).format(l);
	let v = {}, x, S = null;
	for (let t = 0; t < h.length && (x = h[t], v = n[x] || {}, S = v[c], !P(S)); t++) nn(e, c, x, f, "datetime format");
	if (!P(S) || !j(x)) return r ? -1 : c;
	let C = `${x}__${c}`;
	y(d) || (C = `${C}__${JSON.stringify(d)}`);
	let w = s.get(C);
	return w || (w = new Intl.DateTimeFormat(x, b({}, S, d)), s.set(C, w)), p ? w.formatToParts(l) : w.format(l);
}
var ln = [
	"localeMatcher",
	"weekday",
	"era",
	"year",
	"month",
	"day",
	"hour",
	"minute",
	"second",
	"timeZoneName",
	"formatMatcher",
	"hour12",
	"timeZone",
	"dateStyle",
	"timeStyle",
	"calendar",
	"dayPeriod",
	"numberingSystem",
	"hourCycle",
	"fractionalSecondDigits"
];
function un(...e) {
	let [t, n, r, i] = e, a = S(), o = S(), s;
	if (j(t)) {
		let e = t.match(/(\d{4}-\d{2}-\d{2})(T|\s)?(.*)/);
		if (!e) throw bt(yt.INVALID_ISO_DATE_ARGUMENT);
		let n = e[3] ? e[3].trim().startsWith("T") ? `${e[1].trim()}${e[3].trim()}` : `${e[1].trim()}T${e[3].trim()}` : e[1].trim();
		s = new Date(n);
		try {
			s.toISOString();
		} catch {
			throw bt(yt.INVALID_ISO_DATE_ARGUMENT);
		}
	} else if (_(t)) {
		if (isNaN(t.getTime())) throw bt(yt.INVALID_DATE_ARGUMENT);
		s = t;
	} else if (g(t)) s = t;
	else throw bt(yt.INVALID_ARGUMENT);
	return j(n) ? a.key = n : P(n) && Object.keys(n).forEach((e) => {
		ln.includes(e) ? o[e] = n[e] : a[e] = n[e];
	}), j(r) ? a.locale = r : P(r) && (o = r), P(i) && (o = i), [
		a.key || "",
		s,
		a,
		o
	];
}
function dn(e, t, n) {
	let r = e;
	for (let e in n) {
		let n = `${t}__${e}`;
		r.__datetimeFormatters.has(n) && r.__datetimeFormatters.delete(n);
	}
}
function fn(e, ...t) {
	let { numberFormats: n, unresolving: r, fallbackLocale: i, onWarn: a, localeFallbacker: o } = e, { __numberFormatters: s } = e;
	if (!g(t[0])) return "";
	let [c, l, u, d] = mn(...t), f = M(u.missingWarn) ? u.missingWarn : e.missingWarn;
	M(u.fallbackWarn) ? u.fallbackWarn : e.fallbackWarn;
	let p = !!u.part, m = xt(e, u), h = o(e, i, m);
	if (!j(c) || c === "") return new Intl.NumberFormat(m.replace(/!/g, ""), d).format(l);
	let _ = {}, v, x = null;
	for (let t = 0; t < h.length && (v = h[t], _ = n[v] || {}, x = _[c], !P(x)); t++) nn(e, c, v, f, "number format");
	if (!P(x) || !j(v)) return r ? -1 : c;
	let S = `${v}__${c}`;
	y(d) || (S = `${S}__${JSON.stringify(d)}`);
	let C = s.get(S);
	return C || (C = new Intl.NumberFormat(v, b({}, x, d)), s.set(S, C)), p ? C.formatToParts(l) : C.format(l);
}
var pn = [
	"localeMatcher",
	"style",
	"currency",
	"currencyDisplay",
	"currencySign",
	"useGrouping",
	"minimumIntegerDigits",
	"minimumFractionDigits",
	"maximumFractionDigits",
	"minimumSignificantDigits",
	"maximumSignificantDigits",
	"compactDisplay",
	"notation",
	"signDisplay",
	"unit",
	"unitDisplay",
	"roundingMode",
	"roundingPriority",
	"roundingIncrement",
	"trailingZeroDisplay"
];
function mn(...e) {
	let [t, n, r, i] = e, a = S(), o = S();
	if (!g(t)) throw bt(yt.INVALID_ARGUMENT);
	let s = t;
	return j(n) ? a.key = n : P(n) && Object.keys(n).forEach((e) => {
		pn.includes(e) ? o[e] = n[e] : a[e] = n[e];
	}), j(r) ? a.locale = r : P(r) && (o = r), P(i) && (o = i), [
		a.key || "",
		s,
		a,
		o
	];
}
function hn(e, t, n) {
	let r = e;
	for (let e in n) {
		let n = `${t}__${e}`;
		r.__numberFormatters.has(n) && r.__numberFormatters.delete(n);
	}
}
var gn = (e) => e, _n = (e) => "", vn = "text", yn = (e) => e.length === 0 ? "" : ae(e), bn = ie;
function xn(e, t) {
	return e = Math.abs(e), t === 2 ? e === 1 ? 0 : 1 : Math.min(e, 2);
}
function Sn(e) {
	let t = g(e.pluralIndex) ? e.pluralIndex : -1;
	return g(e.named?.count) ? e.named.count : g(e.named?.n) ? e.named.n : t;
}
function Cn(e = {}) {
	let t = e.locale, n = Sn(e), r = j(t) && A(e.pluralRules?.[t]) ? e.pluralRules[t] : xn, i = r === xn ? void 0 : xn, a = (e) => e[r(n, e.length, i)], o = e.list || [], s = (e) => o[e], c = e.named || S();
	g(e.pluralIndex) && (c.count || (c.count = e.pluralIndex), c.n || (c.n = e.pluralIndex));
	let l = (e) => c[e];
	function u(t, n) {
		return (A(e.messages) ? e.messages(t, !!n) : N(e.messages) ? e.messages[t] : !1) || (e.parent ? e.parent.message(t) : _n);
	}
	let d = (t) => e.modifiers ? e.modifiers[t] : gn, f = A(e.processor?.normalize) ? e.processor.normalize : yn, p = A(e.processor?.interpolate) ? e.processor.interpolate : bn, m = {
		list: s,
		named: l,
		plural: a,
		linked: (e, ...t) => {
			let [n, r] = t, i = "text", a = "";
			t.length === 1 ? N(n) ? (a = n.modifier || a, i = n.type || i) : j(n) && (a = n || a) : t.length === 2 && (j(n) && (a = n || a), j(r) && (i = r || i));
			let o = u(e, !0)(m), s = o === "" || o === void 0 ? e : o, c = i === "vnode" && k(s) && a ? s[0] : s;
			return a ? d(a)(c, i) : c;
		},
		message: u,
		type: j(e.processor?.type) ? e.processor.type : vn,
		interpolate: p,
		normalize: f,
		values: b(S(), o, c)
	};
	return m;
}
var wn = () => "", Tn = (e) => A(e);
function En(e, ...t) {
	let { fallbackFormat: n, postTranslation: r, unresolving: i, messageCompiler: a, fallbackLocale: o, messages: s } = e, [c, l] = jn(...t), u = M(l.missingWarn) ? l.missingWarn : e.missingWarn, d = M(l.fallbackWarn) ? l.fallbackWarn : e.fallbackWarn, f = M(l.escapeParameter) ? l.escapeParameter : e.escapeParameter, p = !!l.resolvedMessage, m = j(l.default) || M(l.default) ? M(l.default) ? a ? c : () => c : l.default : n ? a ? c : () => c : null, h = n || m != null && (j(m) || A(m)), g = xt(e, l);
	f && Dn(l);
	let [_, v, y] = p ? [
		c,
		g,
		s[g] || S()
	] : On(e, c, g, o, d, u), b = _, x = c;
	if (!p && !(j(b) || Ge(b) || Tn(b)) && h && (b = m, x = b), !p && (!(j(b) || Ge(b) || Tn(b)) || !j(v))) return i ? -1 : c;
	let C = !1, w = Tn(b) ? b : kn(e, c, v, b, x, () => {
		C = !0;
	});
	if (C) return b;
	let T = An(e, w, Cn(Nn(e, v, y, l))), E = r ? r(T, c) : T;
	return f && j(E) && (E = D(E)), E;
}
function Dn(e) {
	k(e.list) ? e.list = e.list.map((e) => j(e) ? T(e) : e) : N(e.named) && Object.keys(e.named).forEach((t) => {
		j(e.named[t]) && (e.named[t] = T(e.named[t]));
	});
}
function On(e, t, n, r, i, a) {
	let { messages: o, onWarn: s, messageResolver: c, localeFallbacker: l } = e, u = l(e, r, n), d = S(), f, p = null;
	for (let n = 0; n < u.length && (f = u[n], d = o[f] || S(), (p = c(d, t)) === null && (p = d[t]), !(j(p) || Ge(p) || Tn(p))); n++) if (!on(f, u)) {
		let n = nn(e, t, f, a, "translate");
		n !== t && (p = n);
	}
	return [
		p,
		f,
		d
	];
}
function kn(e, t, n, r, i, a) {
	let { messageCompiler: o, warnHtmlMessage: s } = e;
	if (Tn(r)) {
		let e = r;
		return e.locale = e.locale || n, e.key = e.key || t, e;
	}
	if (o == null) {
		let e = (() => r);
		return e.locale = n, e.key = t, e;
	}
	let c = o(r, Mn(e, n, i, r, s, a));
	return c.locale = n, c.key = t, c.source = r, c;
}
function An(e, t, n) {
	return t(n);
}
function jn(...e) {
	let [t, n, r] = e, i = S();
	if (!j(t) && !g(t) && !Tn(t) && !Ge(t)) throw bt(yt.INVALID_ARGUMENT);
	let a = g(t) ? String(t) : (Tn(t), t);
	return g(n) ? i.plural = n : j(n) ? i.default = n : P(n) && !y(n) ? i.named = n : k(n) && (i.list = n), g(r) ? i.plural = r : j(r) ? i.default = r : P(r) && b(i, r), [a, i];
}
function Mn(e, t, n, r, i, a) {
	return {
		locale: t,
		key: n,
		warnHtmlMessage: i,
		onError: (e) => {
			throw a && a(e), e;
		},
		onCacheKey: (e) => m(t, n, e)
	};
}
function Nn(e, t, n, r) {
	let { modifiers: i, pluralRules: a, messageResolver: o, fallbackLocale: s, fallbackWarn: c, missingWarn: l, fallbackContext: u } = e, d = {
		locale: t,
		modifiers: i,
		pluralRules: a,
		messages: (r, i) => {
			let a = o(n, r);
			if (a == null && (u || i)) {
				let [n, , i] = On(u || e, r, t, s, c, l);
				a = n ?? o(i, r);
			}
			if (j(a) || Ge(a)) {
				let n = !1, i = kn(e, r, t, a, r, () => {
					n = !0;
				});
				return n ? wn : i;
			}
			return Tn(a) ? a : wn;
		}
	};
	return e.processor && (d.processor = e.processor), r.list && (d.list = r.list), r.named && (d.named = r.named), g(r.plural) && (d.pluralIndex = r.plural), d;
}
We();
//#endregion
//#region node_modules/@vue/shared/dist/shared.esm-bundler.js
// @__NO_SIDE_EFFECTS__
function Pn(e) {
	let t = /* @__PURE__ */ Object.create(null);
	for (let n of e.split(",")) t[n] = 1;
	return (e) => e in t;
}
var L = {}, Fn = [], In = () => {}, Ln = () => !1, Rn = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && (e.charCodeAt(2) > 122 || e.charCodeAt(2) < 97), zn = (e) => e.startsWith("onUpdate:"), R = Object.assign, Bn = (e, t) => {
	let n = e.indexOf(t);
	n > -1 && e.splice(n, 1);
}, Vn = Object.prototype.hasOwnProperty, z = (e, t) => Vn.call(e, t), B = Array.isArray, Hn = (e) => Yn(e) === "[object Map]", Un = (e) => Yn(e) === "[object Set]", Wn = (e) => Yn(e) === "[object Date]", Gn = (e) => Yn(e) === "[object RegExp]", V = (e) => typeof e == "function", H = (e) => typeof e == "string", Kn = (e) => typeof e == "symbol", U = (e) => typeof e == "object" && !!e, qn = (e) => (U(e) || V(e)) && V(e.then) && V(e.catch), Jn = Object.prototype.toString, Yn = (e) => Jn.call(e), Xn = (e) => Yn(e).slice(8, -1), Zn = (e) => Yn(e) === "[object Object]", Qn = (e) => H(e) && e !== "NaN" && e[0] !== "-" && "" + parseInt(e, 10) === e, $n = /* @__PURE__ */ Pn(",key,ref,ref_for,ref_key,onVnodeBeforeMount,onVnodeMounted,onVnodeBeforeUpdate,onVnodeUpdated,onVnodeBeforeUnmount,onVnodeUnmounted"), er = (e) => {
	let t = /* @__PURE__ */ Object.create(null);
	return ((n) => t[n] || (t[n] = e(n)));
}, tr = /-\w/g, W = er((e) => e.replace(tr, (e) => e.slice(1).toUpperCase())), nr = /\B([A-Z])/g, rr = er((e) => e.replace(nr, "-$1").toLowerCase()), ir = er((e) => e.charAt(0).toUpperCase() + e.slice(1)), ar = er((e) => e ? `on${ir(e)}` : ""), G = (e, t) => !Object.is(e, t), or = (e, ...t) => {
	for (let n = 0; n < e.length; n++) e[n](...t);
}, sr = (e, t, n, r = !1) => {
	Object.defineProperty(e, t, {
		configurable: !0,
		enumerable: !1,
		writable: r,
		value: n
	});
}, cr = (e) => {
	let t = parseFloat(e);
	return isNaN(t) ? e : t;
}, lr = (e) => {
	let t = H(e) ? Number(e) : NaN;
	return isNaN(t) ? e : t;
}, ur, dr = () => ur || (ur = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : {}), fr = /* @__PURE__ */ Pn("Infinity,undefined,NaN,isFinite,isNaN,parseFloat,parseInt,decodeURI,decodeURIComponent,encodeURI,encodeURIComponent,Math,Number,Date,Array,Object,Boolean,String,RegExp,Map,Set,JSON,Intl,BigInt,console,Error,Symbol");
function pr(e) {
	if (B(e)) {
		let t = {};
		for (let n = 0; n < e.length; n++) {
			let r = e[n], i = H(r) ? _r(r) : pr(r);
			if (i) for (let e in i) t[e] = i[e];
		}
		return t;
	}
	if (H(e) || U(e)) return e;
}
var mr = /;(?![^(]*\))/g, hr = /:([^]+)/, gr = /"(?:[^"\\]|\\[^])*"|'(?:[^'\\]|\\[^])*'|\\[^]|\/\*[^]*?\*\//g;
function _r(e) {
	let t = {};
	return e.replace(gr, (e) => e.startsWith("/*") ? "" : e).split(mr).forEach((e) => {
		if (e) {
			let n = e.split(hr);
			n.length > 1 && (t[n[0].trim()] = n[1].trim());
		}
	}), t;
}
function vr(e) {
	if (!e) return "";
	if (H(e)) return e;
	let t = "";
	for (let n in e) {
		let r = e[n];
		if (H(r) || typeof r == "number") {
			let e = n.startsWith("--") ? n : rr(n);
			t += `${e}:${r};`;
		}
	}
	return t;
}
function yr(e) {
	let t = "";
	if (H(e)) t = e;
	else if (B(e)) for (let n = 0; n < e.length; n++) {
		let r = yr(e[n]);
		r && (t += r + " ");
	}
	else if (U(e)) for (let n in e) e[n] && (t += n + " ");
	return t.trim();
}
function br(e) {
	if (!e) return null;
	let { class: t, style: n } = e;
	return t && !H(t) && (e.class = yr(t)), n && (e.style = pr(n)), e;
}
var xr = "itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,readonly", Sr = /* @__PURE__ */ Pn(xr), Cr = /* @__PURE__ */ Pn(xr + ",async,autofocus,autoplay,controls,default,defer,disabled,inert,loop,open,required,reversed,scoped,seamless,checked,muted,multiple,selected");
function wr(e) {
	return !!e || e === "";
}
var Tr = /* @__PURE__ */ Pn("accept,accept-charset,accesskey,action,align,allow,alt,async,autocapitalize,autocomplete,autofocus,autoplay,background,bgcolor,border,buffered,capture,challenge,charset,checked,cite,class,code,codebase,color,cols,colspan,content,contenteditable,contextmenu,controls,coords,crossorigin,csp,data,datetime,decoding,default,defer,dir,dirname,disabled,download,draggable,dropzone,enctype,enterkeyhint,for,form,formaction,formenctype,formmethod,formnovalidate,formtarget,headers,height,hidden,high,href,hreflang,http-equiv,icon,id,importance,inert,integrity,ismap,itemprop,keytype,kind,label,lang,language,loading,list,loop,low,manifest,max,maxlength,minlength,media,min,multiple,muted,name,novalidate,open,optimum,pattern,ping,placeholder,poster,preload,radiogroup,readonly,referrerpolicy,rel,required,reversed,rows,rowspan,sandbox,scope,scoped,selected,shape,size,sizes,slot,span,spellcheck,src,srcdoc,srclang,srcset,start,step,style,summary,tabindex,target,title,translate,type,usemap,value,width,wrap"), Er = /* @__PURE__ */ Pn("xmlns,accent-height,accumulate,additive,alignment-baseline,alphabetic,amplitude,arabic-form,ascent,attributeName,attributeType,azimuth,baseFrequency,baseline-shift,baseProfile,bbox,begin,bias,by,calcMode,cap-height,class,clip,clipPathUnits,clip-path,clip-rule,color,color-interpolation,color-interpolation-filters,color-profile,color-rendering,contentScriptType,contentStyleType,crossorigin,cursor,cx,cy,d,decelerate,descent,diffuseConstant,direction,display,divisor,dominant-baseline,dur,dx,dy,edgeMode,elevation,enable-background,end,exponent,fill,fill-opacity,fill-rule,filter,filterRes,filterUnits,flood-color,flood-opacity,font-family,font-size,font-size-adjust,font-stretch,font-style,font-variant,font-weight,format,from,fr,fx,fy,g1,g2,glyph-name,glyph-orientation-horizontal,glyph-orientation-vertical,glyphRef,gradientTransform,gradientUnits,hanging,height,href,hreflang,horiz-adv-x,horiz-origin-x,id,ideographic,image-rendering,in,in2,intercept,k,k1,k2,k3,k4,kernelMatrix,kernelUnitLength,kerning,keyPoints,keySplines,keyTimes,lang,lengthAdjust,letter-spacing,lighting-color,limitingConeAngle,local,marker-end,marker-mid,marker-start,markerHeight,markerUnits,markerWidth,mask,maskContentUnits,maskUnits,mathematical,max,media,method,min,mode,name,numOctaves,offset,opacity,operator,order,orient,orientation,origin,overflow,overline-position,overline-thickness,panose-1,paint-order,path,pathLength,patternContentUnits,patternTransform,patternUnits,ping,pointer-events,points,pointsAtX,pointsAtY,pointsAtZ,preserveAlpha,preserveAspectRatio,primitiveUnits,r,radius,referrerPolicy,refX,refY,rel,rendering-intent,repeatCount,repeatDur,requiredExtensions,requiredFeatures,restart,result,rotate,rx,ry,scale,seed,shape-rendering,slope,spacing,specularConstant,specularExponent,speed,spreadMethod,startOffset,stdDeviation,stemh,stemv,stitchTiles,stop-color,stop-opacity,strikethrough-position,strikethrough-thickness,string,stroke,stroke-dasharray,stroke-dashoffset,stroke-linecap,stroke-linejoin,stroke-miterlimit,stroke-opacity,stroke-width,style,surfaceScale,systemLanguage,tabindex,tableValues,target,targetX,targetY,text-anchor,text-decoration,text-rendering,textLength,to,transform,transform-origin,type,u1,u2,underline-position,underline-thickness,unicode,unicode-bidi,unicode-range,units-per-em,v-alphabetic,v-hanging,v-ideographic,v-mathematical,values,vector-effect,version,vert-adv-y,vert-origin-x,vert-origin-y,viewBox,viewTarget,visibility,width,widths,word-spacing,writing-mode,x,x-height,x1,x2,xChannelSelector,xlink:actuate,xlink:arcrole,xlink:href,xlink:role,xlink:show,xlink:title,xlink:type,xmlns:xlink,xml:base,xml:lang,xml:space,y,y1,y2,yChannelSelector,z,zoomAndPan");
function Dr(e) {
	if (e == null) return !1;
	let t = typeof e;
	return t === "string" || t === "number" || t === "boolean";
}
var Or = /[ !"#$%&'()*+,./:;<=>?@[\\\]^`{|}~]/g;
function kr(e, t) {
	return e.replace(Or, (e) => t ? e === "\"" ? "\\\\\\\"" : `\\\\${e}` : `\\${e}`);
}
function Ar(e, t, n) {
	if (e.length !== t.length) return !1;
	let r = !0;
	for (let i = 0; r && i < e.length; i++) r = Pr(e[i], t[i], n);
	return r;
}
function jr(e, t, n) {
	if (e.size !== t.size) return !1;
	let r = Array.from(t), i = new Uint8Array(r.length);
	for (let t of e) {
		let e = -1;
		for (let a = 0; a < r.length; a++) if (!i[a] && Pr(t, r[a], n)) {
			e = a;
			break;
		}
		if (e < 0) return !1;
		i[e] = 1;
	}
	return !0;
}
function Mr(e, t, n) {
	let r = Hn(e), i = Hn(t);
	if (r || i || (r = Un(e), i = Un(t), r || i)) return r && i ? jr(e, t, n) : !1;
	if (Object.keys(e).length !== Object.keys(t).length) return !1;
	for (let r in e) {
		let i = e.hasOwnProperty(r), a = t.hasOwnProperty(r);
		if (i && !a || !i && a || !Pr(e[r], t[r], n)) return !1;
	}
	return String(e) === String(t);
}
function Nr(e, t, n, r) {
	n || (n = [/* @__PURE__ */ new Map(), /* @__PURE__ */ new Map()]);
	let [i, a] = n;
	if (i.has(e) || a.has(t)) return i.get(e) === t && a.get(t) === e;
	i.set(e, t), a.set(t, e);
	let o = r(e, t, n);
	return i.delete(e), a.delete(t), o;
}
function Pr(e, t, n) {
	if (e === t) return !0;
	let r = Wn(e), i = Wn(t);
	return r || i ? r && i ? e.getTime() === t.getTime() : !1 : (r = Kn(e), i = Kn(t), r || i ? e === t : (r = B(e), i = B(t), r || i ? r && i ? Nr(e, t, n, Ar) : !1 : (r = U(e), i = U(t), r || i ? !r || !i ? !1 : Nr(e, t, n, Mr) : String(e) === String(t))));
}
function Fr(e, t) {
	return e.findIndex((e) => Pr(e, t));
}
var Ir = (e) => !!(e && e.__v_isRef === !0), Lr = (e) => H(e) ? e : e == null ? "" : B(e) || U(e) && (e.toString === Jn || !V(e.toString)) ? Ir(e) ? Lr(e.value) : JSON.stringify(e, Rr, 2) : String(e), Rr = (e, t) => Ir(t) ? Rr(e, t.value) : Hn(t) ? { [`Map(${t.size})`]: [...t.entries()].reduce((e, [t, n], r) => (e[zr(t, r) + " =>"] = n, e), {}) } : Un(t) ? { [`Set(${t.size})`]: [...t.values()].map((e) => zr(e)) } : Kn(t) ? zr(t) : U(t) && !B(t) && !Zn(t) ? String(t) : t, zr = (e, t = "") => Kn(e) ? `Symbol(${e.description ?? t})` : e;
function Br(e) {
	return e == null ? "initial" : typeof e == "string" ? e === "" ? " " : e : String(e);
}
//#endregion
//#region node_modules/@vue/reactivity/dist/reactivity.esm-bundler.js
var K, Vr = class {
	constructor(e = !1) {
		this.detached = e, this._active = !0, this._on = 0, this.effects = [], this.cleanups = [], this._isPaused = !1, this._warnOnRun = !0, this.__v_skip = !0, !e && K && (K.active ? (this.parent = K, this.index = (K.scopes || (K.scopes = [])).push(this) - 1) : (this._active = !1, this._warnOnRun = !1));
	}
	get active() {
		return this._active;
	}
	pause() {
		if (this._active) {
			this._isPaused = !0;
			let e, t;
			if (this.scopes) {
				let n = this.scopes.slice();
				for (e = 0, t = n.length; e < t; e++) n[e].pause();
			}
			for (e = 0, t = this.effects.length; e < t; e++) this.effects[e].pause();
		}
	}
	resume() {
		if (this._active && this._isPaused) {
			this._isPaused = !1;
			let e, t;
			if (this.scopes) {
				let n = this.scopes.slice();
				for (e = 0, t = n.length; e < t; e++) n[e].resume();
			}
			let n = this.effects.slice();
			for (e = 0, t = n.length; e < t; e++) n[e].resume();
		}
	}
	run(e) {
		if (this._active) {
			let t = K;
			try {
				return K = this, e();
			} finally {
				K = t;
			}
		}
	}
	on() {
		++this._on === 1 && (this.prevScope = K, K = this);
	}
	off() {
		if (this._on > 0 && --this._on === 0) {
			if (K === this) K = this.prevScope;
			else {
				let e = K;
				for (; e;) {
					if (e.prevScope === this) {
						e.prevScope = this.prevScope;
						break;
					}
					e = e.prevScope;
				}
			}
			this.prevScope = void 0;
		}
	}
	stop(e) {
		if (this._active) {
			this._active = !1;
			let t, n;
			for (t = 0, n = this.effects.length; t < n; t++) this.effects[t].stop();
			for (this.effects.length = 0, t = 0, n = this.cleanups.length; t < n; t++) this.cleanups[t]();
			if (this.cleanups.length = 0, this.scopes) {
				let e = this.scopes.slice();
				for (t = 0, n = e.length; t < n; t++) e[t].stop(!0);
				this.scopes.length = 0;
			}
			if (!this.detached && this.parent && !e) {
				let e = this.parent.scopes.pop();
				e && e !== this && (this.parent.scopes[this.index] = e, e.index = this.index);
			}
			this.parent = void 0;
		}
	}
};
function Hr(e) {
	return new Vr(e);
}
function Ur() {
	return K;
}
function Wr(e, t = !1) {
	K && K.cleanups.push(e);
}
var q, Gr = /* @__PURE__ */ new WeakSet(), Kr = class {
	constructor(e) {
		this.fn = e, this.deps = void 0, this.depsTail = void 0, this.flags = 5, this.next = void 0, this.cleanup = void 0, this.scheduler = void 0, K && (K.active ? K.effects.push(this) : this.flags &= -2);
	}
	pause() {
		this.flags |= 64;
	}
	resume() {
		this.flags & 64 && (this.flags &= -65, Gr.has(this) && (Gr.delete(this), this.trigger()));
	}
	notify() {
		this.flags & 2 && !(this.flags & 32) || this.flags & 8 || Xr(this);
	}
	run() {
		if (!(this.flags & 1)) return this.fn();
		this.flags |= 2, di(this), $r(this);
		let e = q, t = si;
		q = this, si = !0;
		try {
			return this.fn();
		} finally {
			ei(this), q = e, si = t, this.flags &= -3;
		}
	}
	stop() {
		if (this.flags & 1) {
			for (let e = this.deps; e; e = e.nextDep) ri(e);
			this.deps = this.depsTail = void 0, di(this), this.onStop && this.onStop(), this.flags &= -2;
		}
	}
	trigger() {
		this.flags & 64 ? Gr.add(this) : this.scheduler ? this.scheduler() : this.runIfDirty();
	}
	runIfDirty() {
		ti(this) && this.run();
	}
	get dirty() {
		return ti(this);
	}
}, qr = 0, Jr, Yr;
function Xr(e, t = !1) {
	e.flags |= 8, t ? (e.next = Yr, Yr = e) : (e.next = Jr, Jr = e);
}
function Zr() {
	qr++;
}
function Qr() {
	if (--qr > 0) return;
	if (Yr) {
		let e = Yr;
		for (Yr = void 0; e;) {
			let t = e.next;
			e.next = void 0, e.flags &= -9, e = t;
		}
	}
	let e;
	for (; Jr;) {
		let t = Jr;
		for (Jr = void 0; t;) {
			let n = t.next;
			if (t.next = void 0, t.flags &= -9, t.flags & 1) try {
				t.trigger();
			} catch (t) {
				e || (e = t);
			}
			t = n;
		}
	}
	if (e) throw e;
}
function $r(e) {
	for (let t = e.deps; t; t = t.nextDep) t.version = -1, t.prevActiveLink = t.dep.activeLink, t.dep.activeLink = t;
}
function ei(e) {
	let t, n = e.depsTail, r = n;
	for (; r;) {
		let e = r.prevDep;
		r.version === -1 ? (r === n && (n = e), ri(r), ii(r)) : t = r, r.dep.activeLink = r.prevActiveLink, r.prevActiveLink = void 0, r = e;
	}
	e.deps = t, e.depsTail = n;
}
function ti(e) {
	for (let t = e.deps; t; t = t.nextDep) if (t.dep.version !== t.version || t.dep.computed && (ni(t.dep.computed) || t.dep.version !== t.version)) return !0;
	return !!e._dirty;
}
function ni(e) {
	if (e.flags & 4 && !(e.flags & 16) || (e.flags &= -17, e.globalVersion === fi) || (e.globalVersion = fi, !e.isSSR && e.flags & 128 && (!e.deps && !e._dirty || !ti(e)))) return;
	e.flags |= 2;
	let t = e.dep, n = q, r = si;
	q = e, si = !0;
	try {
		$r(e);
		let n = e.fn(e._value);
		(t.version === 0 || G(n, e._value)) && (e.flags |= 128, e._value = n, t.version++);
	} catch (e) {
		throw t.version++, e;
	} finally {
		q = n, si = r, ei(e), e.flags &= -3;
	}
}
function ri(e, t = !1) {
	let { dep: n, prevSub: r, nextSub: i } = e;
	if (r && (r.nextSub = i, e.prevSub = void 0), i && (i.prevSub = r, e.nextSub = void 0), n.subs === e && (n.subs = r, !r && n.computed)) {
		n.computed.flags &= -5;
		for (let e = n.computed.deps; e; e = e.nextDep) ri(e, !0);
	}
	!t && !--n.sc && n.map && n.map.delete(n.key);
}
function ii(e) {
	let { prevDep: t, nextDep: n } = e;
	t && (t.nextDep = n, e.prevDep = void 0), n && (n.prevDep = t, e.nextDep = void 0);
}
function ai(e, t) {
	e.effect instanceof Kr && (e = e.effect.fn);
	let n = new Kr(e);
	t && R(n, t);
	try {
		n.run();
	} catch (e) {
		throw n.stop(), e;
	}
	let r = n.run.bind(n);
	return r.effect = n, r;
}
function oi(e) {
	e.effect.stop();
}
var si = !0, ci = [];
function li() {
	ci.push(si), si = !1;
}
function ui() {
	let e = ci.pop();
	si = e === void 0 || e;
}
function di(e) {
	let { cleanup: t } = e;
	if (e.cleanup = void 0, t) {
		let e = q;
		q = void 0;
		try {
			t();
		} finally {
			q = e;
		}
	}
}
var fi = 0, pi = class {
	constructor(e, t) {
		this.sub = e, this.dep = t, this.version = t.version, this.nextDep = this.prevDep = this.nextSub = this.prevSub = this.prevActiveLink = void 0;
	}
}, mi = class {
	constructor(e) {
		this.computed = e, this.version = 0, this.activeLink = void 0, this.subs = void 0, this.map = void 0, this.key = void 0, this.sc = 0, this.__v_skip = !0;
	}
	track(e) {
		if (!q || !si || q === this.computed) return;
		let t = this.activeLink;
		if (t === void 0 || t.sub !== q) t = this.activeLink = new pi(q, this), q.deps ? (t.prevDep = q.depsTail, q.depsTail.nextDep = t, q.depsTail = t) : q.deps = q.depsTail = t, hi(t);
		else if (t.version === -1 && (t.version = this.version, t.nextDep)) {
			let e = t.nextDep;
			e.prevDep = t.prevDep, t.prevDep && (t.prevDep.nextDep = e), t.prevDep = q.depsTail, t.nextDep = void 0, q.depsTail.nextDep = t, q.depsTail = t, q.deps === t && (q.deps = e);
		}
		return t;
	}
	trigger(e) {
		this.version++, fi++, this.notify(e);
	}
	notify(e) {
		Zr();
		try {
			for (let e = this.subs; e; e = e.prevSub) e.sub.notify() && e.sub.dep.notify();
		} finally {
			Qr();
		}
	}
};
function hi(e) {
	if (e.dep.sc++, e.sub.flags & 4) {
		let t = e.dep.computed;
		if (t && !e.dep.subs) {
			t.flags |= 20;
			for (let e = t.deps; e; e = e.nextDep) hi(e);
		}
		let n = e.dep.subs;
		n !== e && (e.prevSub = n, n && (n.nextSub = e)), e.dep.subs = e;
	}
}
var gi = /* @__PURE__ */ new WeakMap(), _i = /* @__PURE__ */ Symbol(""), vi = /* @__PURE__ */ Symbol(""), yi = /* @__PURE__ */ Symbol("");
function bi(e, t, n) {
	if (si && q) {
		let t = gi.get(e);
		t || gi.set(e, t = /* @__PURE__ */ new Map());
		let r = t.get(n);
		r || (t.set(n, r = new mi()), r.map = t, r.key = n), r.track();
	}
}
function xi(e, t, n, r, i, a) {
	let o = gi.get(e);
	if (!o) {
		fi++;
		return;
	}
	let s = (e) => {
		e && e.trigger();
	};
	if (Zr(), t === "clear") o.forEach(s);
	else {
		let i = B(e), a = i && Qn(n);
		if (i && n === "length") {
			let e = Number(r);
			o.forEach((t, n) => {
				(n === "length" || n === yi || !Kn(n) && n >= e) && s(t);
			});
		} else switch ((n !== void 0 || o.has(void 0)) && s(o.get(n)), a && s(o.get(yi)), t) {
			case "add":
				i ? a && s(o.get("length")) : (s(o.get(_i)), Hn(e) && s(o.get(vi)));
				break;
			case "delete":
				i || (s(o.get(_i)), Hn(e) && s(o.get(vi)));
				break;
			case "set": Hn(e) && s(o.get(_i));
		}
	}
	Qr();
}
function Si(e, t) {
	let n = gi.get(e);
	return n && n.get(t);
}
function Ci(e) {
	let t = /* @__PURE__ */ J(e);
	return t === e || (bi(t, "iterate", yi), /* @__PURE__ */ da(e)) ? t : /* @__PURE__ */ ua(e) ? /* @__PURE__ */ la(e) ? t.map((e) => ha(ma(e))) : t.map(ha) : t.map(ma);
}
function wi(e) {
	return bi(e = /* @__PURE__ */ J(e), "iterate", yi), e;
}
function Ti(e, t) {
	return /* @__PURE__ */ ua(e) ? ha(/* @__PURE__ */ la(e) ? ma(t) : t) : ma(t);
}
var Ei = {
	__proto__: null,
	[Symbol.iterator]() {
		return Di(this, Symbol.iterator, (e) => Ti(this, e));
	},
	concat(...e) {
		return Ci(this).concat(...e.map((e) => B(e) ? Ci(e) : e));
	},
	entries() {
		return Di(this, "entries", (e) => (e[1] = Ti(this, e[1]), e));
	},
	every(e, t) {
		return ki(this, "every", e, t, void 0, arguments);
	},
	filter(e, t) {
		return ki(this, "filter", e, t, (e) => e.map((e) => Ti(this, e)), arguments);
	},
	find(e, t) {
		return ki(this, "find", e, t, (e) => Ti(this, e), arguments);
	},
	findIndex(e, t) {
		return ki(this, "findIndex", e, t, void 0, arguments);
	},
	findLast(e, t) {
		return ki(this, "findLast", e, t, (e) => Ti(this, e), arguments);
	},
	findLastIndex(e, t) {
		return ki(this, "findLastIndex", e, t, void 0, arguments);
	},
	forEach(e, t) {
		return ki(this, "forEach", e, t, void 0, arguments);
	},
	includes(...e) {
		return ji(this, "includes", e);
	},
	indexOf(...e) {
		return ji(this, "indexOf", e);
	},
	join(e) {
		return Ci(this).join(e);
	},
	lastIndexOf(...e) {
		return ji(this, "lastIndexOf", e);
	},
	map(e, t) {
		return ki(this, "map", e, t, void 0, arguments);
	},
	pop() {
		return Mi(this, "pop");
	},
	push(...e) {
		return Mi(this, "push", e);
	},
	reduce(e, ...t) {
		return Ai(this, "reduce", e, t);
	},
	reduceRight(e, ...t) {
		return Ai(this, "reduceRight", e, t);
	},
	shift() {
		return Mi(this, "shift");
	},
	some(e, t) {
		return ki(this, "some", e, t, void 0, arguments);
	},
	splice(...e) {
		return Mi(this, "splice", e);
	},
	toReversed() {
		return Ci(this).toReversed();
	},
	toSorted(e) {
		return Ci(this).toSorted(e);
	},
	toSpliced(...e) {
		return Ci(this).toSpliced(...e);
	},
	unshift(...e) {
		return Mi(this, "unshift", e);
	},
	values() {
		return Di(this, "values", (e) => Ti(this, e));
	}
};
function Di(e, t, n) {
	let r = wi(e), i = r[t]();
	return r !== e && !/* @__PURE__ */ da(e) && (i._next = i.next, i.next = () => {
		let e = i._next();
		return e.done || (e.value = n(e.value)), e;
	}), i;
}
var Oi = Array.prototype;
function ki(e, t, n, r, i, a) {
	let o = wi(e), s = o !== e && !/* @__PURE__ */ da(e), c = o[t];
	if (c !== Oi[t]) {
		let t = c.apply(e, a);
		return s ? ma(t) : t;
	}
	let l = n;
	o !== e && (s ? l = function(t, r) {
		return n.call(this, Ti(e, t), r, e);
	} : n.length > 2 && (l = function(t, r) {
		return n.call(this, t, r, e);
	}));
	let u = c.call(o, l, r);
	return s && i ? i(u) : u;
}
function Ai(e, t, n, r) {
	let i = wi(e), a = i !== e && !/* @__PURE__ */ da(e), o = n, s = !1;
	i !== e && (a ? (s = r.length === 0, o = function(t, r, i) {
		return s && (s = !1, t = Ti(e, t)), n.call(this, t, Ti(e, r), i, e);
	}) : n.length > 3 && (o = function(t, r, i) {
		return n.call(this, t, r, i, e);
	}));
	let c = i[t](o, ...r);
	return s ? Ti(e, c) : c;
}
function ji(e, t, n) {
	let r = /* @__PURE__ */ J(e);
	bi(r, "iterate", yi);
	let i = r[t](...n);
	return (i === -1 || i === !1) && /* @__PURE__ */ fa(n[0]) ? (n[0] = /* @__PURE__ */ J(n[0]), r[t](...n)) : i;
}
function Mi(e, t, n = []) {
	li(), Zr();
	let r = (/* @__PURE__ */ J(e))[t].apply(e, n);
	return Qr(), ui(), r;
}
var Ni = /* @__PURE__ */ Pn("__proto__,__v_isRef,__isVue"), Pi = new Set(/* @__PURE__ */ Object.getOwnPropertyNames(Symbol).filter((e) => e !== "arguments" && e !== "caller").map((e) => Symbol[e]).filter(Kn));
function Fi(e) {
	Kn(e) || (e = String(e));
	let t = /* @__PURE__ */ J(this);
	return bi(t, "has", e), t.hasOwnProperty(e);
}
var Ii = class {
	constructor(e = !1, t = !1) {
		this._isReadonly = e, this._isShallow = t;
	}
	get(e, t, n) {
		if (t === "__v_skip") return e.__v_skip;
		let r = this._isReadonly, i = this._isShallow;
		if (t === "__v_isReactive") return !r;
		if (t === "__v_isReadonly") return r;
		if (t === "__v_isShallow") return i;
		if (t === "__v_raw") return n === (r ? i ? na : ta : i ? ea : $i).get(e) || Object.getPrototypeOf(e) === Object.getPrototypeOf(n) ? e : void 0;
		let a = B(e);
		if (!r) {
			let e;
			if (a && (e = Ei[t])) return e;
			if (t === "hasOwnProperty") return Fi;
		}
		let o = Reflect.get(e, t, /* @__PURE__ */ Y(e) ? e : n);
		if ((Kn(t) ? Pi.has(t) : Ni(t)) || (r || bi(e, "get", t), i)) return o;
		if (/* @__PURE__ */ Y(o)) {
			let e = a && Qn(t) ? o : o.value;
			return r && U(e) ? /* @__PURE__ */ oa(e) : e;
		}
		return U(o) ? r ? /* @__PURE__ */ oa(o) : /* @__PURE__ */ ia(o) : o;
	}
}, Li = class extends Ii {
	constructor(e = !1) {
		super(!1, e);
	}
	set(e, t, n, r) {
		let i = e[t], a = B(e) && Qn(t);
		if (!this._isShallow) {
			let e = /* @__PURE__ */ ua(i);
			if (!/* @__PURE__ */ da(n) && !/* @__PURE__ */ ua(n) && (i = /* @__PURE__ */ J(i), n = /* @__PURE__ */ J(n)), !a && /* @__PURE__ */ Y(i) && !/* @__PURE__ */ Y(n)) return e || (i.value = n), !0;
		}
		let o = a ? Number(t) < e.length : z(e, t), s = Reflect.set(e, t, n, /* @__PURE__ */ Y(e) ? e : r);
		return e === /* @__PURE__ */ J(r) && s && (o ? G(n, i) && xi(e, "set", t, n, i) : xi(e, "add", t, n)), s;
	}
	deleteProperty(e, t) {
		let n = z(e, t), r = e[t], i = Reflect.deleteProperty(e, t);
		return i && n && xi(e, "delete", t, void 0, r), i;
	}
	has(e, t) {
		let n = Reflect.has(e, t);
		return (!Kn(t) || !Pi.has(t)) && bi(e, "has", t), n;
	}
	ownKeys(e) {
		return bi(e, "iterate", B(e) ? "length" : _i), Reflect.ownKeys(e);
	}
}, Ri = class extends Ii {
	constructor(e = !1) {
		super(!0, e);
	}
	set(e, t) {
		return !0;
	}
	deleteProperty(e, t) {
		return !0;
	}
}, zi = /* @__PURE__ */ new Li(), Bi = /* @__PURE__ */ new Ri(), Vi = /* @__PURE__ */ new Li(!0), Hi = /* @__PURE__ */ new Ri(!0), Ui = (e) => e, Wi = (e) => Reflect.getPrototypeOf(e);
function Gi(e, t, n) {
	return function(...r) {
		let i = this.__v_raw, a = /* @__PURE__ */ J(i), o = Hn(a), s = e === "entries" || e === Symbol.iterator && o, c = e === "keys" && o, l = i[e](...r), u = n ? Ui : t ? ha : ma;
		return !t && bi(a, "iterate", c ? vi : _i), R(Object.create(l), { next() {
			let { value: e, done: t } = l.next();
			return t ? {
				value: e,
				done: t
			} : {
				value: s ? [u(e[0]), u(e[1])] : u(e),
				done: t
			};
		} });
	};
}
function Ki(e) {
	return function(...t) {
		return e === "delete" ? !1 : e === "clear" ? void 0 : this;
	};
}
function qi(e, t) {
	let n = {
		get(n) {
			let r = this.__v_raw, i = /* @__PURE__ */ J(r), a = /* @__PURE__ */ J(n);
			e || (G(n, a) && bi(i, "get", n), bi(i, "get", a));
			let { has: o } = Wi(i), s = t ? Ui : e ? ha : ma;
			if (o.call(i, n)) return s(r.get(n));
			if (o.call(i, a)) return s(r.get(a));
			r !== i && r.get(n);
		},
		get size() {
			let t = this.__v_raw;
			return !e && bi(/* @__PURE__ */ J(t), "iterate", _i), t.size;
		},
		has(t) {
			let n = this.__v_raw, r = /* @__PURE__ */ J(n), i = /* @__PURE__ */ J(t);
			return e || (G(t, i) && bi(r, "has", t), bi(r, "has", i)), t === i ? n.has(t) : n.has(t) || n.has(i);
		},
		forEach(n, r) {
			let i = this, a = i.__v_raw, o = /* @__PURE__ */ J(a), s = t ? Ui : e ? ha : ma;
			return !e && bi(o, "iterate", _i), a.forEach((e, t) => n.call(r, s(e), s(t), i));
		}
	};
	return R(n, e ? {
		add: Ki("add"),
		set: Ki("set"),
		delete: Ki("delete"),
		clear: Ki("clear")
	} : {
		add(e) {
			let n = /* @__PURE__ */ J(this), r = Wi(n), i = /* @__PURE__ */ J(e), a = !t && !/* @__PURE__ */ da(e) && !/* @__PURE__ */ ua(e) ? i : e;
			return r.has.call(n, a) || G(e, a) && r.has.call(n, e) || G(i, a) && r.has.call(n, i) || (n.add(a), xi(n, "add", a, a)), this;
		},
		set(e, n) {
			!t && !/* @__PURE__ */ da(n) && !/* @__PURE__ */ ua(n) && (n = /* @__PURE__ */ J(n));
			let r = /* @__PURE__ */ J(this), { has: i, get: a } = Wi(r), o = i.call(r, e);
			o || (e = /* @__PURE__ */ J(e), o = i.call(r, e));
			let s = a.call(r, e);
			return r.set(e, n), o ? G(n, s) && xi(r, "set", e, n, s) : xi(r, "add", e, n), this;
		},
		delete(e) {
			let t = /* @__PURE__ */ J(this), { has: n, get: r } = Wi(t), i = n.call(t, e);
			i || (e = /* @__PURE__ */ J(e), i = n.call(t, e));
			let a = r ? r.call(t, e) : void 0, o = t.delete(e);
			return i && xi(t, "delete", e, void 0, a), o;
		},
		clear() {
			let e = /* @__PURE__ */ J(this), t = e.size !== 0, n = e.clear();
			return t && xi(e, "clear", void 0, void 0, void 0), n;
		}
	}), [
		"keys",
		"values",
		"entries",
		Symbol.iterator
	].forEach((r) => {
		n[r] = Gi(r, e, t);
	}), n;
}
function Ji(e, t) {
	let n = qi(e, t);
	return (t, r, i) => r === "__v_isReactive" ? !e : r === "__v_isReadonly" ? e : r === "__v_raw" ? t : Reflect.get(z(n, r) && r in t ? n : t, r, i);
}
var Yi = { get: /* @__PURE__ */ Ji(!1, !1) }, Xi = { get: /* @__PURE__ */ Ji(!1, !0) }, Zi = { get: /* @__PURE__ */ Ji(!0, !1) }, Qi = { get: /* @__PURE__ */ Ji(!0, !0) }, $i = /* @__PURE__ */ new WeakMap(), ea = /* @__PURE__ */ new WeakMap(), ta = /* @__PURE__ */ new WeakMap(), na = /* @__PURE__ */ new WeakMap();
function ra(e) {
	switch (e) {
		case "Object":
		case "Array": return 1;
		case "Map":
		case "Set":
		case "WeakMap":
		case "WeakSet": return 2;
		default: return 0;
	}
}
// @__NO_SIDE_EFFECTS__
function ia(e) {
	return /* @__PURE__ */ ua(e) ? e : ca(e, !1, zi, Yi, $i);
}
// @__NO_SIDE_EFFECTS__
function aa(e) {
	return ca(e, !1, Vi, Xi, ea);
}
// @__NO_SIDE_EFFECTS__
function oa(e) {
	return ca(e, !0, Bi, Zi, ta);
}
// @__NO_SIDE_EFFECTS__
function sa(e) {
	return ca(e, !0, Hi, Qi, na);
}
function ca(e, t, n, r, i) {
	if (!U(e) || e.__v_raw && !(t && e.__v_isReactive) || e.__v_skip || !Object.isExtensible(e)) return e;
	let a = i.get(e);
	if (a) return a;
	let o = ra(Xn(e));
	if (o === 0) return e;
	let s = new Proxy(e, o === 2 ? r : n);
	return i.set(e, s), s;
}
// @__NO_SIDE_EFFECTS__
function la(e) {
	return /* @__PURE__ */ ua(e) ? /* @__PURE__ */ la(e.__v_raw) : !!(e && e.__v_isReactive);
}
// @__NO_SIDE_EFFECTS__
function ua(e) {
	return !!(e && e.__v_isReadonly);
}
// @__NO_SIDE_EFFECTS__
function da(e) {
	return !!(e && e.__v_isShallow);
}
// @__NO_SIDE_EFFECTS__
function fa(e) {
	return e ? !!e.__v_raw : !1;
}
// @__NO_SIDE_EFFECTS__
function J(e) {
	let t = e && e.__v_raw;
	return t ? /* @__PURE__ */ J(t) : e;
}
function pa(e) {
	return !z(e, "__v_skip") && Object.isExtensible(e) && sr(e, "__v_skip", !0), e;
}
var ma = (e) => U(e) ? /* @__PURE__ */ ia(e) : e, ha = (e) => U(e) ? /* @__PURE__ */ oa(e) : e;
// @__NO_SIDE_EFFECTS__
function Y(e) {
	return e ? e.__v_isRef === !0 : !1;
}
// @__NO_SIDE_EFFECTS__
function ga(e) {
	return va(e, !1);
}
// @__NO_SIDE_EFFECTS__
function _a(e) {
	return va(e, !0);
}
function va(e, t) {
	return /* @__PURE__ */ Y(e) ? e : new ya(e, t);
}
var ya = class {
	constructor(e, t) {
		this.dep = new mi(), this.__v_isRef = !0, this.__v_isShallow = !1, this._rawValue = t ? e : /* @__PURE__ */ J(e), this._value = t ? e : ma(e), this.__v_isShallow = t;
	}
	get value() {
		return this.dep.track(), this._value;
	}
	set value(e) {
		let t = this._rawValue, n = this.__v_isShallow || /* @__PURE__ */ da(e) || /* @__PURE__ */ ua(e);
		e = n ? e : /* @__PURE__ */ J(e), G(e, t) && (this._rawValue = e, this._value = n ? e : ma(e), this.dep.trigger());
	}
};
function ba(e) {
	e.dep && e.dep.trigger();
}
function xa(e) {
	return /* @__PURE__ */ Y(e) ? e.value : e;
}
function Sa(e) {
	return V(e) ? e() : xa(e);
}
var Ca = {
	get: (e, t, n) => t === "__v_raw" ? e : xa(Reflect.get(e, t, n)),
	set: (e, t, n, r) => {
		let i = e[t];
		return /* @__PURE__ */ Y(i) && !/* @__PURE__ */ Y(n) ? (i.value = n, !0) : Reflect.set(e, t, n, r);
	}
};
function wa(e) {
	return /* @__PURE__ */ la(e) ? e : new Proxy(e, Ca);
}
var Ta = class {
	constructor(e) {
		this.__v_isRef = !0, this._value = void 0;
		let t = this.dep = new mi(), { get: n, set: r } = e(t.track.bind(t), t.trigger.bind(t));
		this._get = n, this._set = r;
	}
	get value() {
		return this._value = this._get();
	}
	set value(e) {
		this._set(e);
	}
};
function Ea(e) {
	return new Ta(e);
}
// @__NO_SIDE_EFFECTS__
function Da(e) {
	let t = B(e) ? Array(e.length) : {};
	for (let n in e) t[n] = ja(e, n);
	return t;
}
var Oa = class {
	constructor(e, t, n) {
		this._object = e, this._defaultValue = n, this.__v_isRef = !0, this._value = void 0, this._key = Kn(t) ? t : String(t), this._raw = /* @__PURE__ */ J(e);
		let r = !0, i = e;
		if (!B(e) || Kn(this._key) || !Qn(this._key)) do
			r = !/* @__PURE__ */ fa(i) || /* @__PURE__ */ da(i);
		while (r && (i = i.__v_raw));
		this._shallow = r;
	}
	get value() {
		let e = this._object[this._key];
		return this._shallow && (e = xa(e)), this._value = e === void 0 ? this._defaultValue : e;
	}
	set value(e) {
		if (this._shallow && /* @__PURE__ */ Y(this._raw[this._key])) {
			let t = this._object[this._key];
			if (/* @__PURE__ */ Y(t)) {
				t.value = e;
				return;
			}
		}
		this._object[this._key] = e;
	}
	get dep() {
		return Si(this._raw, this._key);
	}
}, ka = class {
	constructor(e) {
		this._getter = e, this.__v_isRef = !0, this.__v_isReadonly = !0, this._value = void 0;
	}
	get value() {
		return this._value = this._getter();
	}
};
// @__NO_SIDE_EFFECTS__
function Aa(e, t, n) {
	return /* @__PURE__ */ Y(e) ? e : V(e) ? new ka(e) : U(e) && arguments.length > 1 ? ja(e, t, n) : /* @__PURE__ */ ga(e);
}
function ja(e, t, n) {
	return new Oa(e, t, n);
}
var Ma = class {
	constructor(e, t, n) {
		this.fn = e, this.setter = t, this._value = void 0, this.dep = new mi(this), this.__v_isRef = !0, this.deps = void 0, this.depsTail = void 0, this.flags = 16, this.globalVersion = fi - 1, this.next = void 0, this.effect = this, this.__v_isReadonly = !t, this.isSSR = n;
	}
	notify() {
		if (this.flags |= 16, !(this.flags & 8) && q !== this) return Xr(this, !0), !0;
	}
	get value() {
		let e = this.dep.track();
		return ni(this), e && (e.version = this.dep.version), this._value;
	}
	set value(e) {
		this.setter && this.setter(e);
	}
};
// @__NO_SIDE_EFFECTS__
function Na(e, t, n = !1) {
	let r, i;
	return V(e) ? r = e : (r = e.get, i = e.set), new Ma(r, i, n);
}
var Pa = {
	GET: "get",
	HAS: "has",
	ITERATE: "iterate"
}, Fa = {
	SET: "set",
	ADD: "add",
	DELETE: "delete",
	CLEAR: "clear"
}, Ia = {}, La = /* @__PURE__ */ new WeakMap(), Ra = void 0;
function za() {
	return Ra;
}
function Ba(e, t = !1, n = Ra) {
	if (n) {
		let t = La.get(n);
		t || La.set(n, t = []), t.push(e);
	}
}
function Va(e, t, n = L) {
	let { immediate: r, deep: i, once: a, scheduler: o, augmentJob: s, call: c } = n, l = (e) => i ? e : /* @__PURE__ */ da(e) || i === !1 || i === 0 ? Ha(e, 1) : Ha(e), u, d, f, p, m = !1, h = !1;
	if (/* @__PURE__ */ Y(e) ? (d = () => e.value, m = /* @__PURE__ */ da(e)) : /* @__PURE__ */ la(e) ? (d = () => l(e), m = !0) : B(e) ? (h = !0, m = e.some((e) => /* @__PURE__ */ la(e) || /* @__PURE__ */ da(e)), d = () => e.map((e) => {
		if (/* @__PURE__ */ Y(e)) return e.value;
		if (/* @__PURE__ */ la(e)) return l(e);
		if (V(e)) return c ? c(e, 2) : e();
	})) : d = V(e) ? t ? c ? () => c(e, 2) : e : () => {
		if (f) {
			li();
			try {
				f();
			} finally {
				ui();
			}
		}
		let t = Ra;
		Ra = u;
		try {
			return c ? c(e, 3, [p]) : e(p);
		} finally {
			Ra = t;
		}
	} : In, t && i) {
		let e = d, t = i === !0 ? Infinity : i;
		d = () => Ha(e(), t);
	}
	let g = Ur(), _ = () => {
		u.stop(), g && g.active && Bn(g.effects, u);
	};
	if (a && t) {
		let e = t;
		t = (...t) => {
			let n = e(...t);
			return _(), n;
		};
	}
	let v = h ? Array(e.length).fill(Ia) : Ia, y = (e) => {
		if (u.flags & 1 && (u.dirty || e)) {
			if (t) {
				let n = u.run();
				if (e || i || m || (h ? n.some((e, t) => G(e, v[t])) : G(n, v))) {
					f && f();
					let e = Ra;
					Ra = u;
					try {
						let e = [
							n,
							v === Ia ? void 0 : h && v[0] === Ia ? [] : v,
							p
						];
						v = n, c ? c(t, 3, e) : t(...e);
					} finally {
						Ra = e;
					}
				}
			} else u.run();
		}
	};
	return s && s(y), u = new Kr(d), u.scheduler = o ? () => o(y, !1) : y, p = (e) => Ba(e, !1, u), f = u.onStop = () => {
		let e = La.get(u);
		if (e) {
			if (c) c(e, 4);
			else for (let t of e) t();
			La.delete(u);
		}
	}, t ? r ? y(!0) : v = u.run() : o ? o(y.bind(null, !0), !0) : u.run(), _.pause = u.pause.bind(u), _.resume = u.resume.bind(u), _.stop = _, _;
}
function Ha(e, t = Infinity, n) {
	if (t <= 0 || !U(e) || e.__v_skip || (n = n || /* @__PURE__ */ new Map(), (n.get(e) || 0) >= t)) return e;
	if (n.set(e, t), t--, /* @__PURE__ */ Y(e)) Ha(e.value, t, n);
	else if (B(e)) for (let r = 0; r < e.length; r++) Ha(e[r], t, n);
	else if (Un(e) || Hn(e)) e.forEach((e) => {
		Ha(e, t, n);
	});
	else if (Zn(e)) {
		for (let r in e) Ha(e[r], t, n);
		for (let r of Object.getOwnPropertySymbols(e)) Object.prototype.propertyIsEnumerable.call(e, r) && Ha(e[r], t, n);
	}
	return e;
}
//#endregion
//#region node_modules/@vue/runtime-core/dist/runtime-core.esm-bundler.js
var Ua = [];
function Wa(e) {
	Ua.push(e);
}
function Ga() {
	Ua.pop();
}
var Ka = !1;
function qa(e, ...t) {
	if (Ka) return;
	Ka = !0, li();
	let n = Ua.length ? Ua[Ua.length - 1].component : null, r = n && n.appContext.config.warnHandler, i = Ja();
	if (r) no(r, n, 11, [
		e + t.map((e) => e.toString?.call(e) ?? JSON.stringify(e)).join(""),
		n && n.proxy,
		i.map(({ vnode: e }) => `at <${Vd(n, e.type)}>`).join("\n"),
		i
	]);
	else {
		let n = [`[Vue warn]: ${e}`, ...t];
		i.length && n.push("\n", ...Ya(i)), console.warn(...n);
	}
	ui(), Ka = !1;
}
function Ja() {
	let e = Ua[Ua.length - 1];
	if (!e) return [];
	let t = [];
	for (; e;) {
		let n = t[0];
		n && n.vnode === e ? n.recurseCount++ : t.push({
			vnode: e,
			recurseCount: 0
		});
		let r = e.component && e.component.parent;
		e = r && r.vnode;
	}
	return t;
}
function Ya(e) {
	let t = [];
	return e.forEach((e, n) => {
		t.push(...n === 0 ? [] : ["\n"], ...Xa(e));
	}), t;
}
function Xa({ vnode: e, recurseCount: t }) {
	let n = t > 0 ? `... (${t} recursive calls)` : "", r = e.component ? e.component.parent == null : !1, i = ` at <${Vd(e.component, e.type, r)}`, a = ">" + n;
	return e.props ? [
		i,
		...Za(e.props),
		a
	] : [i + a];
}
function Za(e) {
	let t = [], n = Object.keys(e);
	return n.slice(0, 3).forEach((n) => {
		t.push(...Qa(n, e[n]));
	}), n.length > 3 && t.push(" ..."), t;
}
function Qa(e, t, n) {
	return H(t) ? (t = JSON.stringify(t), n ? t : [`${e}=${t}`]) : typeof t == "number" || typeof t == "boolean" || t == null ? n ? t : [`${e}=${t}`] : /* @__PURE__ */ Y(t) ? (t = Qa(e, /* @__PURE__ */ J(t.value), !0), n ? t : [
		`${e}=Ref<`,
		t,
		">"
	]) : V(t) ? [`${e}=fn${t.name ? `<${t.name}>` : ""}`] : (t = /* @__PURE__ */ J(t), n ? t : [`${e}=`, t]);
}
function $a(e, t) {}
var eo = {
	SETUP_FUNCTION: 0,
	0: "SETUP_FUNCTION",
	RENDER_FUNCTION: 1,
	1: "RENDER_FUNCTION",
	NATIVE_EVENT_HANDLER: 5,
	5: "NATIVE_EVENT_HANDLER",
	COMPONENT_EVENT_HANDLER: 6,
	6: "COMPONENT_EVENT_HANDLER",
	VNODE_HOOK: 7,
	7: "VNODE_HOOK",
	DIRECTIVE_HOOK: 8,
	8: "DIRECTIVE_HOOK",
	TRANSITION_HOOK: 9,
	9: "TRANSITION_HOOK",
	APP_ERROR_HANDLER: 10,
	10: "APP_ERROR_HANDLER",
	APP_WARN_HANDLER: 11,
	11: "APP_WARN_HANDLER",
	FUNCTION_REF: 12,
	12: "FUNCTION_REF",
	ASYNC_COMPONENT_LOADER: 13,
	13: "ASYNC_COMPONENT_LOADER",
	SCHEDULER: 14,
	14: "SCHEDULER",
	COMPONENT_UPDATE: 15,
	15: "COMPONENT_UPDATE",
	APP_UNMOUNT_CLEANUP: 16,
	16: "APP_UNMOUNT_CLEANUP"
}, to = {
	sp: "serverPrefetch hook",
	bc: "beforeCreate hook",
	c: "created hook",
	bm: "beforeMount hook",
	m: "mounted hook",
	bu: "beforeUpdate hook",
	u: "updated",
	bum: "beforeUnmount hook",
	um: "unmounted hook",
	a: "activated hook",
	da: "deactivated hook",
	ec: "errorCaptured hook",
	rtc: "renderTracked hook",
	rtg: "renderTriggered hook",
	0: "setup function",
	1: "render function",
	2: "watcher getter",
	3: "watcher callback",
	4: "watcher cleanup function",
	5: "native event handler",
	6: "component event handler",
	7: "vnode hook",
	8: "directive hook",
	9: "transition hook",
	10: "app errorHandler",
	11: "app warnHandler",
	12: "ref function",
	13: "async component loader",
	14: "scheduler flush",
	15: "component update",
	16: "app unmount cleanup function"
};
function no(e, t, n, r) {
	try {
		return r ? e(...r) : e();
	} catch (e) {
		io(e, t, n);
	}
}
function ro(e, t, n, r) {
	if (V(e)) {
		let i = no(e, t, n, r);
		return i && qn(i) && i.catch((e) => {
			io(e, t, n);
		}), i;
	}
	if (B(e)) {
		let i = [];
		for (let a = 0; a < e.length; a++) i.push(ro(e[a], t, n, r));
		return i;
	}
}
function io(e, t, n, r = !0) {
	let i = t ? t.vnode : null, { errorHandler: a, throwUnhandledErrorInProduction: o } = t && t.appContext.config || L;
	if (t) {
		let r = t.parent, i = t.proxy, o = `https://vuejs.org/error-reference/#runtime-${n}`;
		for (; r;) {
			let t = r.ec;
			if (t) {
				for (let n = 0; n < t.length; n++) if (t[n](e, i, o) === !1) return;
			}
			r = r.parent;
		}
		if (a) {
			li(), no(a, null, 10, [
				e,
				i,
				o
			]), ui();
			return;
		}
	}
	ao(e, n, i, r, o);
}
function ao(e, t, n, r = !0, i = !1) {
	if (i) throw e;
	console.error(e);
}
var oo = [], so = -1, co = [], lo = null, uo = 0, fo = /* @__PURE__ */ Promise.resolve(), po = null;
function mo(e) {
	let t = po || fo;
	return e ? t.then(this ? e.bind(this) : e) : t;
}
function ho(e) {
	let t = so + 1, n = oo.length;
	for (; t < n;) {
		let r = t + n >>> 1, i = oo[r], a = xo(i);
		a < e || a === e && i.flags & 2 ? t = r + 1 : n = r;
	}
	return t;
}
function go(e) {
	if (!(e.flags & 1)) {
		let t = xo(e), n = oo[oo.length - 1];
		!n || !(e.flags & 2) && t >= xo(n) ? oo.push(e) : oo.splice(ho(t), 0, e), e.flags |= 1, _o();
	}
}
function _o() {
	po || (po = fo.then(So));
}
function vo(e) {
	if (!B(e)) lo && e.id === -1 ? lo.splice(uo + 1, 0, e) : e.flags & 1 || (co.push(e), e.flags |= 1);
	else for (let t = 0; t < e.length; t++) co.push(e[t]);
	_o();
}
function yo(e, t, n = so + 1) {
	for (; n < oo.length; n++) {
		let t = oo[n];
		if (t && t.flags & 2) {
			if (e && t.id !== e.uid) continue;
			oo.splice(n, 1), n--, t.flags & 4 && (t.flags &= -2), t(), t.flags & 4 || (t.flags &= -2);
		}
	}
}
function bo(e) {
	if (co.length) {
		let e = [...new Set(co)].sort((e, t) => xo(e) - xo(t));
		if (co.length = 0, lo) {
			for (let t = 0; t < e.length; t++) lo.push(e[t]);
			return;
		}
		for (lo = e, uo = 0; uo < lo.length; uo++) {
			let e = lo[uo];
			e.flags & 4 && (e.flags &= -2), e.flags & 8 || e(), e.flags &= -2;
		}
		lo = null, uo = 0;
	}
}
var xo = (e) => e.id == null ? e.flags & 2 ? -1 : Infinity : e.id;
function So(e) {
	try {
		for (so = 0; so < oo.length; so++) {
			let e = oo[so];
			e && !(e.flags & 8) && (e.flags & 4 && (e.flags &= -2), no(e, e.i, e.i ? 15 : 14), e.flags & 4 || (e.flags &= -2));
		}
	} finally {
		for (; so < oo.length; so++) {
			let e = oo[so];
			e && (e.flags &= -2);
		}
		so = -1, oo.length = 0, bo(e), po = null, (oo.length || co.length) && So(e);
	}
}
var Co, wo = [], To = !1;
function Eo(e, ...t) {
	Co ? Co.emit(e, ...t) : To || wo.push({
		event: e,
		args: t
	});
}
function Do(e, t) {
	Co = e, Co ? (Co.enabled = !0, wo.forEach(({ event: e, args: t }) => Co.emit(e, ...t)), wo = []) : typeof window < "u" && window.HTMLElement && !(window.navigator?.userAgent)?.includes("jsdom") ? ((t.__VUE_DEVTOOLS_HOOK_REPLAY__ = t.__VUE_DEVTOOLS_HOOK_REPLAY__ || []).push((e) => {
		Do(e, t);
	}), setTimeout(() => {
		Co || (t.__VUE_DEVTOOLS_HOOK_REPLAY__ = null, To = !0, wo = []);
	}, 3e3)) : (To = !0, wo = []);
}
function Oo(e, t) {
	Eo("app:init", e, t, {
		Fragment: Z,
		Text: Hu,
		Comment: Q,
		Static: Uu
	});
}
function ko(e) {
	Eo("app:unmount", e);
}
var Ao = /* @__PURE__ */ Po("component:added"), jo = /* @__PURE__ */ Po("component:updated"), Mo = /* @__PURE__ */ Po("component:removed"), No = (e) => {
	Co && typeof Co.cleanupBuffer == "function" && !Co.cleanupBuffer(e) && Mo(e);
};
// @__NO_SIDE_EFFECTS__
function Po(e) {
	return (t) => {
		Eo(e, t.appContext.app, t.uid, t.parent ? t.parent.uid : void 0, t);
	};
}
function Fo(e, t, n) {
	Eo("component:emit", e.appContext.app, e, t, n);
}
var Io = null, Lo = null;
function Ro(e) {
	let t = Io;
	return Io = e, Lo = e && e.type.__scopeId || null, t;
}
function zo(e) {
	Lo = e;
}
function Bo() {
	Lo = null;
}
var Vo = (e) => Ho;
function Ho(e, t = Io, n) {
	if (!t || e._n) return e;
	let r = (...n) => {
		r._d && Yu(-1);
		let i = Ro(t), a = Wu.length, o;
		try {
			o = e(...n);
		} finally {
			for (let e = Wu.length; e > a; e--) qu();
			Ro(i), r._d && Yu(1);
		}
		return __VUE_PROD_DEVTOOLS__ && jo(t), o;
	};
	return r._n = !0, r._c = !0, r._d = !0, r;
}
function Uo(e, t) {
	if (Io === null) return e;
	let n = Ld(Io), r = e.dirs || (e.dirs = []);
	for (let e = 0; e < t.length; e++) {
		let [i, a, o, s = L] = t[e];
		i && (V(i) && (i = {
			mounted: i,
			updated: i
		}), i.deep && Ha(a), r.push({
			dir: i,
			instance: n,
			value: a,
			oldValue: void 0,
			arg: o,
			modifiers: s
		}));
	}
	return e;
}
function Wo(e, t, n, r) {
	let i = e.dirs, a = t && t.dirs;
	for (let o = 0; o < i.length; o++) {
		let s = i[o];
		a && (s.oldValue = a[o].value);
		let c = s.dir[r];
		c && (li(), ro(c, n, 8, [
			e.el,
			s,
			e,
			t
		]), ui());
	}
}
function Go(e, t) {
	if (yd) {
		let n = yd.provides, r = yd.parent && yd.parent.provides;
		r === n && (n = yd.provides = Object.create(r)), n[e] = t;
	}
}
function Ko(e, t, n = !1) {
	let r = bd();
	if (r || Rl) {
		let i = Rl ? Rl._context.provides : r ? r.parent == null || r.ce ? r.vnode.appContext && r.vnode.appContext.provides : r.parent.provides : void 0;
		if (i && e in i) return i[e];
		if (arguments.length > 1) return n && V(t) ? t.call(r && r.proxy) : t;
	}
}
function qo() {
	return !!(bd() || Rl);
}
var Jo = /* @__PURE__ */ Symbol.for("v-scx"), Yo = () => Ko(Jo);
function Xo(e, t) {
	return es(e, null, t);
}
function Zo(e, t) {
	return es(e, null, { flush: "post" });
}
function Qo(e, t) {
	return es(e, null, { flush: "sync" });
}
function $o(e, t, n) {
	return es(e, t, n);
}
function es(e, t, n = L) {
	let { immediate: r, deep: i, flush: a, once: o } = n, s = R({}, n), c = t && r || !t && a !== "post", l;
	if (Ed) {
		if (a === "sync") {
			let e = Yo();
			l = e.__watcherHandles || (e.__watcherHandles = []);
		} else if (!c) {
			let e = () => {};
			return e.stop = In, e.resume = In, e.pause = In, e;
		}
	}
	let u = yd;
	s.call = (e, t, n) => ro(e, u, t, n);
	let d = !1;
	a === "post" ? s.scheduler = (e) => {
		X(e, u && u.suspense);
	} : a !== "sync" && (d = !0, s.scheduler = (e, t) => {
		t ? e() : go(e);
	}), s.augmentJob = (e) => {
		t && (e.flags |= 4), d && (e.flags |= 2, u && (e.id = u.uid, e.i = u));
	};
	let f = Va(e, t, s);
	return Ed && (l ? l.push(f) : c && f()), f;
}
function ts(e, t, n) {
	let r = this.proxy, i = H(e) ? e.includes(".") ? ns(r, e) : () => r[e] : e.bind(r, r), a;
	V(t) ? a = t : (a = t.handler, n = t);
	let o = Cd(this), s = es(i, a.bind(r), n);
	return o(), s;
}
function ns(e, t) {
	let n = t.split(".");
	return () => {
		let t = e;
		for (let e = 0; e < n.length && t; e++) t = t[n[e]];
		return t;
	};
}
var rs = /* @__PURE__ */ new WeakMap(), is = /* @__PURE__ */ Symbol("_vte"), as = (e) => e.__isTeleport, os = (e) => e && (e.disabled || e.disabled === ""), ss = (e) => e && (e.defer || e.defer === ""), cs = (e) => typeof SVGElement < "u" && e instanceof SVGElement, ls = (e) => typeof MathMLElement == "function" && e instanceof MathMLElement, us = (e, t) => {
	let n = e && e.to;
	return H(n) ? t ? t(n) : null : n;
}, ds = {
	name: "Teleport",
	__isTeleport: !0,
	process(e, t, n, r, i, a, o, s, c, l) {
		let { mc: u, pc: d, pbc: f, o: { insert: p, querySelector: m, createText: h, createComment: g, parentNode: _ } } = l, v = os(t.props), { dynamicChildren: y } = t, b = (e, t, n) => {
			e.shapeFlag & 16 && u(e.children, t, n, i, a, o, s, c);
		}, x = (e = t) => {
			let n = os(e.props), r = e.target = us(e.props, m), a = gs(r, e, h, p);
			r && (o !== "svg" && cs(r) ? o = "svg" : o !== "mathml" && ls(r) && (o = "mathml"), i && i.isCE && (i.ce._teleportTargets || (i.ce._teleportTargets = /* @__PURE__ */ new Set())).add(r), n || (b(e, r, a), hs(e, !1)));
		}, S = (e) => {
			let t = () => {
				if (rs.get(e) === t) {
					if (rs.delete(e), os(e.props)) {
						let t = _(e.el) || n;
						b(e, t, e.anchor), hs(e, !0);
					}
					x(e);
				}
			};
			rs.set(e, t), X(t, a);
		};
		if (e == null) {
			let e = t.el = h(""), i = t.anchor = h("");
			if (p(e, n, r), p(i, n, r), ss(t.props) || a && a.pendingBranch) {
				S(t);
				return;
			}
			v && (b(t, n, i), hs(t, !0)), x();
		} else {
			t.el = e.el;
			let r = t.anchor = e.anchor, u = rs.get(e);
			if (u) {
				u.flags |= 8, rs.delete(e), S(t);
				return;
			}
			t.targetStart = e.targetStart;
			let p = t.target = e.target, h = t.targetAnchor = e.targetAnchor, g = os(e.props), _ = g ? n : p, b = g ? r : h;
			if (o === "svg" || cs(p) ? o = "svg" : (o === "mathml" || ls(p)) && (o = "mathml"), y ? (f(e.dynamicChildren, y, _, i, a, o, s), wu(e, t, !0)) : c || d(e, t, _, b, i, a, o, s, !1), v) g ? t.props && e.props && t.props.to !== e.props.to && (t.props.to = e.props.to) : fs(t, n, r, l, 1);
			else if ((t.props && t.props.to) !== (e.props && e.props.to)) {
				let e = us(t.props, m);
				e && (t.target = e, fs(t, e, null, l, 0));
			} else g && fs(t, p, h, l, 1);
			hs(t, v);
		}
	},
	remove(e, t, n, { um: r, o: { remove: i } }, a) {
		let { shapeFlag: o, children: s, anchor: c, targetStart: l, targetAnchor: u, target: d, props: f } = e, p = os(f), m = a || !p, h = rs.get(e);
		if (h && (h.flags |= 8, rs.delete(e)), d && (i(l), i(u)), a && i(c), !h && (p || d) && o & 16) for (let e = 0; e < s.length; e++) {
			let i = s[e];
			r(i, t, n, m, !!i.dynamicChildren);
		}
	},
	move: fs,
	hydrate: ps
};
function fs(e, t, n, { o: { insert: r }, m: i }, a = 2) {
	a === 0 && r(e.targetAnchor, t, n);
	let { el: o, anchor: s, shapeFlag: c, children: l, props: u } = e, d = a === 2;
	if (d && r(o, t, n), !rs.has(e) && (!d || os(u)) && c & 16) for (let e = 0; e < l.length; e++) i(l[e], t, n, 2);
	d && r(s, t, n);
}
function ps(e, t, n, r, i, a, { o: { nextSibling: o, parentNode: s, querySelector: c, insert: l, createText: u } }, d) {
	function f(e, n) {
		let r = n;
		for (; r;) {
			if (r && r.nodeType === 8) {
				if (r.data === "teleport start anchor") t.targetStart = r;
				else if (r.data === "teleport anchor") {
					t.targetAnchor = r, e._lpa = t.targetAnchor && o(t.targetAnchor);
					break;
				}
			}
			r = o(r);
		}
	}
	function p(e, t) {
		t.anchor = d(o(e), t, s(e), n, r, i, a);
	}
	let m = t.target = us(t.props, c), h = os(t.props);
	if (m) {
		let c = m._lpa || m.firstChild;
		t.shapeFlag & 16 && (h ? (p(e, t), f(m, c), t.targetAnchor || gs(m, t, u, l, s(e) === m ? e : null)) : (t.anchor = o(e), f(m, c), t.targetAnchor || gs(m, t, u, l), d(c && o(c), t, m, n, r, i, a))), hs(t, h);
	} else h && t.shapeFlag & 16 && (p(e, t), t.targetStart = e, t.targetAnchor = o(e));
	return t.anchor && o(t.anchor);
}
var ms = ds;
function hs(e, t) {
	let n = e.ctx;
	if (n && n.ut) {
		let r, i;
		for (t ? (r = e.el, i = e.anchor) : (r = e.targetStart, i = e.targetAnchor); r && r !== i;) r.nodeType === 1 && r.setAttribute("data-v-owner", n.uid), r = r.nextSibling;
		n.ut();
	}
}
function gs(e, t, n, r, i = null) {
	let a = t.targetStart = n(""), o = t.targetAnchor = n("");
	return a[is] = o, e && (r(a, e, i), r(o, e, i)), o;
}
var _s = /* @__PURE__ */ Symbol("_leaveCb"), vs = /* @__PURE__ */ Symbol("_enterCb");
function ys() {
	let e = {
		isMounted: !1,
		isLeaving: !1,
		isUnmounting: !1,
		leavingVNodes: /* @__PURE__ */ new Map()
	};
	return Mc(() => {
		e.isMounted = !0;
	}), Fc(() => {
		e.isUnmounting = !0;
	}), e;
}
var bs = [Function, Array], xs = {
	mode: String,
	appear: Boolean,
	persisted: Boolean,
	onBeforeEnter: bs,
	onEnter: bs,
	onAfterEnter: bs,
	onEnterCancelled: bs,
	onBeforeLeave: bs,
	onLeave: bs,
	onAfterLeave: bs,
	onLeaveCancelled: bs,
	onBeforeAppear: bs,
	onAppear: bs,
	onAfterAppear: bs,
	onAppearCancelled: bs
}, Ss = (e) => {
	let t = e.subTree;
	return t.component ? Ss(t.component) : t;
}, Cs = {
	name: "BaseTransition",
	props: xs,
	setup(e, { slots: t }) {
		let n = bd(), r = ys();
		return () => {
			let i = t.default && js(t.default(), !0), a = i && i.length ? ws(i) : n.subTree ? ud() : void 0;
			if (!a) return;
			let o = /* @__PURE__ */ J(e), { mode: s } = o;
			if (r.isLeaving) return Os(a);
			let c = ks(a);
			if (!c) return Os(a);
			let l = Ds(c, o, r, n, (e) => l = e);
			c.type !== Q && As(c, l);
			let u = n.subTree && ks(n.subTree);
			if (u && u.type !== Q && !ed(u, c) && Ss(n).type !== Q) {
				let e = Ds(u, o, r, n);
				if (As(u, e), s === "out-in" && c.type !== Q) return r.isLeaving = !0, e.afterLeave = () => {
					r.isLeaving = !1, n.job.flags & 8 || n.update(), delete e.afterLeave, u = void 0;
				}, Os(a);
				s === "in-out" && c.type !== Q ? e.delayLeave = (e, t, n) => {
					let i = Es(r, u);
					i[String(u.key)] = u, e[_s] = () => {
						t(), e[_s] = void 0, delete l.delayedLeave, u = void 0;
					}, l.delayedLeave = () => {
						n(), delete l.delayedLeave, u = void 0;
					};
				} : u = void 0;
			} else u && (u = void 0);
			return a;
		};
	}
};
function ws(e) {
	let t = e[0];
	if (e.length > 1) {
		for (let n of e) if (n.type !== Q) {
			t = n;
			break;
		}
	}
	return t;
}
var Ts = Cs;
function Es(e, t) {
	let { leavingVNodes: n } = e, r = n.get(t.type);
	return r || (r = /* @__PURE__ */ Object.create(null), n.set(t.type, r)), r;
}
function Ds(e, t, n, r, i) {
	let { appear: a, mode: o, persisted: s = !1, onBeforeEnter: c, onEnter: l, onAfterEnter: u, onEnterCancelled: d, onBeforeLeave: f, onLeave: p, onAfterLeave: m, onLeaveCancelled: h, onBeforeAppear: g, onAppear: _, onAfterAppear: v, onAppearCancelled: y } = t, b = String(e.key), x = Es(n, e), S = (e, t) => {
		e && ro(e, r, 9, t);
	}, C = (e, t) => {
		let n = t[1];
		S(e, t), B(e) ? e.every((e) => e.length <= 1) && n() : e.length <= 1 && n();
	}, w = {
		mode: o,
		persisted: s,
		beforeEnter(t) {
			let r = c;
			if (!n.isMounted) {
				if (a) r = g || c;
				else return;
			}
			t[_s] && t[_s](!0);
			let i = x[b];
			i && ed(e, i) && i.el[_s] && i.el[_s](), S(r, [t]);
		},
		enter(t) {
			if (x[b] === e) return;
			let r = l, i = u, o = d;
			if (!n.isMounted) {
				if (a) r = _ || l, i = v || u, o = y || d;
				else return;
			}
			let s = !1;
			t[vs] = (e) => {
				s || (s = !0, S(e ? o : i, [t]), w.delayedLeave && w.delayedLeave(), t[vs] = void 0);
			};
			let c = t[vs].bind(null, !1);
			r ? C(r, [t, c]) : c();
		},
		leave(t, r) {
			let i = String(e.key);
			if (t[vs] && t[vs](!0), n.isUnmounting) return r();
			S(f, [t]);
			let a = !1;
			t[_s] = (n) => {
				a || (a = !0, r(), S(n ? h : m, [t]), t[_s] = void 0, x[i] === e && delete x[i]);
			};
			let o = t[_s].bind(null, !1);
			x[i] = e, p ? C(p, [t, o]) : o();
		},
		clone(e) {
			let a = Ds(e, t, n, r, i);
			return i && i(a), a;
		}
	};
	return w;
}
function Os(e) {
	if (bc(e)) return e = sd(e), e.children = null, e;
}
function ks(e) {
	if (!bc(e)) return as(e.type) && e.children ? ws(e.children) : e;
	if (e.component) return e.component.subTree;
	let { shapeFlag: t, children: n } = e;
	if (n) {
		if (t & 16) return n[0];
		if (t & 32 && V(n.default)) return n.default();
	}
}
function As(e, t) {
	if (e.shapeFlag & 6 && e.component) {
		e.transition = t;
		let n = e.component.subTree;
		As(as(n.type) && ks(n) || n, t);
	} else e.shapeFlag & 128 ? (e.ssContent.transition = t.clone(e.ssContent), e.ssFallback.transition = t.clone(e.ssFallback)) : e.transition = t;
}
function js(e, t = !1, n) {
	let r = [], i = 0;
	for (let a = 0; a < e.length; a++) {
		let o = e[a], s = n == null ? o.key : String(n) + String(o.key == null ? a : o.key);
		o.type === Z ? (o.patchFlag & 128 && i++, r = r.concat(js(o.children, t, s))) : (t || o.type !== Q) && r.push(s == null ? o : sd(o, { key: s }));
	}
	if (i > 1) for (let e = 0; e < r.length; e++) r[e].patchFlag = -2;
	return r;
}
// @__NO_SIDE_EFFECTS__
function Ms(e, t) {
	return V(e) ? /* @__PURE__ */ R({ name: e.name }, t, { setup: e }) : e;
}
function Ns() {
	let e = bd();
	return e ? (e.appContext.config.idPrefix || "v") + "-" + e.ids[0] + e.ids[1]++ : "";
}
function Ps(e) {
	e.ids = [
		e.ids[0] + e.ids[2]++ + "-",
		0,
		0
	];
}
function Fs(e) {
	let t = bd(), n = /* @__PURE__ */ _a(null);
	if (t) {
		let r = t.refs === L ? t.refs = {} : t.refs;
		Object.defineProperty(r, e, {
			enumerable: !0,
			get: () => n.value,
			set: (e) => n.value = e
		});
	}
	return n;
}
function Is(e, t) {
	let n;
	return !!((n = Object.getOwnPropertyDescriptor(e, t)) && !n.configurable);
}
var Ls = /* @__PURE__ */ new WeakMap();
function Rs(e, t, n, r, i = !1) {
	if (B(e)) {
		e.forEach((e, a) => Rs(e, t && (B(t) ? t[a] : t), n, r, i));
		return;
	}
	if (_c(r) && !i) {
		r.shapeFlag & 512 && r.type.__asyncResolved && r.component.subTree.component && Rs(e, t, n, r.component.subTree);
		return;
	}
	let a = r.shapeFlag & 4 ? Ld(r.component) : r.el, o = i ? null : a, { i: s, r: c } = e, l = t && t.r, u = s.refs === L ? s.refs = {} : s.refs, d = s.setupState, f = /* @__PURE__ */ J(d), p = d === L ? Ln : (e) => !Is(u, e) && z(f, e), m = (e, t) => !(t && Is(u, t));
	if (l != null && l !== c) {
		if (zs(t), H(l)) u[l] = null, p(l) && (d[l] = null);
		else if (/* @__PURE__ */ Y(l)) {
			let e = t;
			m(l, e.k) && (l.value = null), e.k && (u[e.k] = null);
		}
	}
	if (V(c)) no(c, s, 12, [o, u]);
	else {
		let t = H(c), r = /* @__PURE__ */ Y(c);
		if (t || r) {
			let s = () => {
				if (e.f) {
					let n = t ? p(c) ? d[c] : u[c] : m(c) || !e.k ? c.value : u[e.k];
					if (i) B(n) && Bn(n, a);
					else if (B(n)) n.includes(a) || n.push(a);
					else if (t) u[c] = [a], p(c) && (d[c] = u[c]);
					else {
						let t = [a];
						m(c, e.k) && (c.value = t), e.k && (u[e.k] = t);
					}
				} else t ? (u[c] = o, p(c) && (d[c] = o)) : r && (m(c, e.k) && (c.value = o), e.k && (u[e.k] = o));
			};
			if (o) {
				let t = () => {
					s(), Ls.delete(e);
				};
				t.id = -1, Ls.set(e, t), X(t, n);
			} else zs(e), s();
		}
	}
}
function zs(e) {
	let t = Ls.get(e);
	t && (t.flags |= 8, Ls.delete(e));
}
var Bs = !1, Vs = () => {
	Bs || (console.error("Hydration completed but contains mismatches."), Bs = !0);
}, Hs = (e) => e.namespaceURI.includes("svg") && e.tagName !== "foreignObject", Us = (e) => e.namespaceURI.includes("MathML"), Ws = (e) => {
	if (e.nodeType === 1) {
		if (Hs(e)) return "svg";
		if (Us(e)) return "mathml";
	}
}, Gs = (e) => e.nodeType === 8;
function Ks(e) {
	let { mt: t, p: n, o: { patchProp: r, createText: i, nextSibling: a, parentNode: o, remove: s, insert: c, createComment: l } } = e, u = (e, t) => {
		t.hasChildNodes() ? (d(t.firstChild, e, null, null, null), bo(), t._vnode = e) : (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Attempting to hydrate existing markup but container is empty. Performing full mount instead."), n(null, e, t), bo(), t._vnode = e);
	}, d = (n, r, s, l, u, y = !1) => {
		y = y || !!r.dynamicChildren;
		let b = Gs(n) && n.data === "[", x = () => h(n, r, s, l, u, b), { type: S, ref: C, shapeFlag: w, patchFlag: T } = r, E = n.nodeType;
		r.el = n, __VUE_PROD_DEVTOOLS__ && (sr(n, "__vnode", r, !0), sr(n, "__vueParentComponent", s, !0)), T === -2 && (y = !1, r.dynamicChildren = null);
		let D = null;
		switch (S) {
			case Hu:
				E === 3 ? (n.data !== r.children && (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Hydration text mismatch in", n.parentNode, `
  - rendered on server: ${JSON.stringify(n.data)}
  - expected on client: ${JSON.stringify(r.children)}`), Vs(), n.data = r.children), D = a(n)) : r.children === "" ? (c(r.el = i(""), o(n), n), D = n) : D = x();
				break;
			case Q:
				v(n) ? (D = a(n), _(r.el = n.content.firstChild, n, s)) : D = E !== 8 || b ? x() : a(n);
				break;
			case Uu:
				if (b && (n = a(n), E = n.nodeType), E === 1 || E === 3) {
					D = n;
					let e = !r.children.length;
					for (let t = 0; t < r.staticCount; t++) e && (r.children += D.nodeType === 1 ? D.outerHTML : D.data), t === r.staticCount - 1 && (r.anchor = D), D = a(D);
					return b ? a(D) : D;
				}
				x();
				break;
			case Z:
				D = b ? m(n, r, s, l, u, y) : x();
				break;
			default: if (w & 1) D = (E !== 1 || r.type.toLowerCase() !== n.tagName.toLowerCase()) && !v(n) ? x() : f(n, r, s, l, u, y);
			else if (w & 6) {
				r.slotScopeIds = u;
				let e = o(n);
				if (D = b ? g(n) : Gs(n) && n.data === "teleport start" ? g(n, n.data, "teleport end") : a(n), t(r, e, null, s, l, Ws(e), y), (_c(r) || r.component.asyncDep) && !r.component.subTree) {
					let t;
					b ? (t = $(Uu), t.anchor = D ? D.previousSibling : e.lastChild) : t = n.nodeType === 3 ? cd("") : $(n.nodeType === 8 ? Q : "div"), t.el = n, r.component.subTree = t;
				}
			} else w & 64 ? D = E === 8 ? r.type.hydrate(n, r, s, l, u, y, e, p) : x() : w & 128 ? D = r.type.hydrate(n, r, s, l, Ws(o(n)), u, y, e, d) : __VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Invalid HostVNode type:", S, `(${typeof S})`);
		}
		return C != null && Rs(C, null, l, r), D;
	}, f = (e, t, n, i, a, o) => {
		o = o || !!t.dynamicChildren;
		let { type: c, dynamicProps: l, props: u, patchFlag: d, shapeFlag: f, dirs: m, transition: h } = t, g = c === "input" || c === "option", y = !!l;
		if (g || y || d !== -1) {
			m && Wo(t, null, n, "created");
			let c = !1;
			if (v(e)) {
				c = Cu(null, h) && n && n.vnode.props && n.vnode.props.appear;
				let r = e.content.firstChild;
				if (c) {
					let e = r.getAttribute("class");
					e && (r.$cls = e), h.beforeEnter(r);
				}
				_(r, e, n), t.el = e = r;
			}
			if (f & 16 && !(u && (u.innerHTML || u.textContent))) {
				let r = p(e.firstChild, t, e, n, i, a, o);
				for (r && !ic(e, 1) && (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Hydration children mismatch on", e, "\nServer rendered element contains more child nodes than client vdom."), Vs()); r;) {
					let e = r;
					r = r.nextSibling, s(e);
				}
			} else if (f & 8) {
				let n = t.children;
				n[0] === "\n" && (e.tagName === "PRE" || e.tagName === "TEXTAREA") && (n = n.slice(1));
				let { textContent: r } = e;
				r !== n && r !== n.replace(/\r\n|\r/g, "\n") && (ic(e, 0) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Hydration text content mismatch on", e, `
  - rendered on server: ${r}
  - expected on client: ${n}`), Vs()), e.textContent = t.children);
			}
			if (u) {
				if (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ || g || y || !o || d & 48) {
					let i = e.tagName.includes("-"), a = e.namespaceURI.includes("svg") ? "svg" : e.namespaceURI.includes("MathML") ? "mathml" : void 0;
					for (let o in u) if (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && !(m && m.some((e) => e.dir.created)) && Ys(e, o, u[o], t, n) && Vs(), g && (o.endsWith("value") || o === "indeterminate") || Rn(o) && !$n(o) || o[0] === "." || i && !$n(o) || l && l.includes(o)) {
						if (Js(e, o, u[o])) continue;
						r(e, o, null, u[o], a, n);
					}
				} else if (u.onClick) r(e, "onClick", null, u.onClick, void 0, n);
				else if (d & 4 && /* @__PURE__ */ la(u.style)) for (let e in u.style) u.style[e];
			}
			let b;
			(b = u && u.onVnodeBeforeMount) && hd(b, n, t), m && Wo(t, null, n, "beforeMount"), ((b = u && u.onVnodeMounted) || m || c) && zu(() => {
				b && hd(b, n, t), c && h.enter(e), m && Wo(t, null, n, "mounted");
			}, i);
		}
		return e.nextSibling;
	}, p = (e, t, r, o, s, l, u) => {
		u = u || !!t.dynamicChildren;
		let f = t.children, p = f.length, m = !1;
		for (let t = 0; t < p; t++) {
			let h = u ? f[t] : f[t] = dd(f[t]), g = h.type === Hu;
			e ? (g && !u && t + 1 < p && dd(f[t + 1]).type === Hu && (c(i(e.data.slice(h.children.length)), r, a(e)), e.data = h.children), e = d(e, h, o, s, l, u)) : g && !h.children ? c(h.el = i(""), r) : (m || (m = !0, ic(r, 1) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Hydration children mismatch on", r, "\nServer rendered element contains fewer child nodes than client vdom."), Vs())), n(null, h, r, null, o, s, Ws(r), l));
		}
		return e;
	}, m = (e, t, n, r, i, s) => {
		let { slotScopeIds: u } = t;
		u && (i = i ? i.concat(u) : u);
		let d = o(e), f = p(a(e), t, d, n, r, i, s);
		return f && Gs(f) && f.data === "]" ? a(t.anchor = f) : (Vs(), c(t.anchor = l("]"), d, f), f);
	}, h = (e, t, r, i, c, l) => {
		if (oc(e, t) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && qa("Hydration node mismatch:\n- rendered on server:", e, e.nodeType === 3 ? "(text)" : Gs(e) && e.data === "[" ? "(start of fragment)" : "", "\n- expected on client:", t.type), Vs()), t.el = null, l) {
			let t = g(e);
			for (;;) {
				let n = a(e);
				if (n && n !== t) s(n);
				else break;
			}
		}
		let u = a(e), d = o(e);
		return s(e), n(null, t, d, u, r, i, Ws(d), c), r && (r.vnode.el = t.el, Ql(r, t.el)), u;
	}, g = (e, t = "[", n = "]") => {
		let r = 0;
		for (; e;) if (e = a(e), e && Gs(e) && (e.data === t && r++, e.data === n)) {
			if (r === 0) return a(e);
			r--;
		}
		return e;
	}, _ = (e, t, n) => {
		let r = t.parentNode;
		r && r.replaceChild(e, t);
		let i = n;
		for (; i;) i.vnode.el === t && (i.vnode.el = i.subTree.el = e), i = i.parent;
	}, v = (e) => e.nodeType === 1 && e.tagName === "TEMPLATE";
	return [u, d];
}
var qs = /* @__PURE__ */ new Set([
	"src",
	"srcset",
	"href",
	"poster"
]);
function Js(e, t, n) {
	return qs.has(t) ? e.getAttribute(t) === (n == null ? null : `${n}`) : !1;
}
function Ys(e, t, n, r, i) {
	let a, o, s, c;
	if (t === "class") e.$cls ? (s = e.$cls, delete e.$cls) : s = e.getAttribute("class"), c = yr(n), Qs(Zs(s || ""), Zs(c)) || (a = 2, o = "class");
	else if (t === "style") {
		s = e.getAttribute("style") || "", c = H(n) ? n : vr(pr(n));
		let t = $s(s), l = $s(c);
		if (r.dirs) for (let { dir: e, value: t } of r.dirs) e.name === "show" && !t && l.set("display", "none");
		i && tc(i, r, l), ec(t, l) || (a = 3, o = "style");
	} else (e instanceof SVGElement && Er(t) || e instanceof HTMLElement && (Cr(t) || Tr(t))) && (t === "hidden" ? (s = Xs(e.getAttribute(t)), c = Xs(n)) : Cr(t) ? (s = e.hasAttribute(t), c = wr(n)) : n == null ? (s = e.hasAttribute(t), c = !1) : (s = e.hasAttribute(t) ? e.getAttribute(t) : t === "value" && e.tagName === "TEXTAREA" && e.value, c = Dr(n) ? String(n) : !1), s !== c && (a = 4, o = t));
	if (a != null && !ic(e, a)) {
		let t = (e) => e === !1 ? "(not rendered)" : `${o}="${e}"`;
		return qa(`Hydration ${rc[a]} mismatch on`, e, `
  - rendered on server: ${t(s)}
  - expected on client: ${t(c)}
  Note: this mismatch is check-only. The DOM will not be rectified in production due to performance overhead.
  You should fix the source of the mismatch.`), !0;
	}
	return !1;
}
function Xs(e) {
	return Dr(e) ? H(e) ? e.toLowerCase() === "until-found" ? "until-found" : "" : wr(e) ? "" : !1 : !1;
}
function Zs(e) {
	return new Set(e.trim().split(/\s+/));
}
function Qs(e, t) {
	if (e.size !== t.size) return !1;
	for (let n of e) if (!t.has(n)) return !1;
	return !0;
}
function $s(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e.split(";")) {
		let [e, r] = n.split(":");
		e = e.trim(), r = r && r.trim(), e && r && t.set(e, r);
	}
	return t;
}
function ec(e, t) {
	if (e.size !== t.size) return !1;
	for (let [n, r] of e) if (r !== t.get(n)) return !1;
	return !0;
}
function tc(e, t, n) {
	let r = e.subTree;
	if (e.getCssVars && (t === r || r && r.type === Z && r.children.includes(t))) {
		let t = e.getCssVars();
		for (let e in t) {
			let r = Br(t[e]);
			n.set(`--${kr(e, !1)}`, r);
		}
	}
	t === r && e.parent && tc(e.parent, e.vnode, n);
}
var nc = "data-allow-mismatch", rc = {
	0: "text",
	1: "children",
	2: "class",
	3: "style",
	4: "attribute"
};
function ic(e, t) {
	if (t === 0 || t === 1) for (; e && !e.hasAttribute(nc);) e = e.parentElement;
	return ac(e && e.getAttribute(nc), t);
}
function ac(e, t) {
	if (e == null) return !1;
	if (e === "") return !0;
	{
		let n = e.split(",");
		return t === 0 && n.includes("children") ? !0 : n.includes(rc[t]);
	}
}
function oc(e, t) {
	return ic(e.parentElement, 1) || sc(e) || cc(t);
}
function sc(e) {
	return e.nodeType === 1 && ac(e.getAttribute(nc), 1);
}
function cc({ props: e }) {
	let t = e && e[nc];
	return typeof t == "string" && ac(t, 1);
}
var lc = dr().requestIdleCallback || ((e) => setTimeout(e, 1)), uc = dr().cancelIdleCallback || ((e) => clearTimeout(e)), dc = (e = 1e4) => (t) => {
	let n = lc(t, { timeout: e });
	return () => uc(n);
};
function fc(e) {
	let { top: t, left: n, bottom: r, right: i } = e.getBoundingClientRect(), { innerHeight: a, innerWidth: o } = window;
	return (t > 0 && t < a || r > 0 && r < a) && (n > 0 && n < o || i > 0 && i < o);
}
var pc = (e) => (t, n) => {
	let r = new IntersectionObserver((e) => {
		for (let n of e) if (n.isIntersecting) {
			r.disconnect(), t();
			break;
		}
	}, e);
	return n((e) => {
		if (e instanceof Element) {
			if (fc(e)) return t(), r.disconnect(), !1;
			r.observe(e);
		}
	}), () => r.disconnect();
}, mc = (e) => (t) => {
	if (e) {
		let n = matchMedia(e);
		if (n.matches) t();
		else return n.addEventListener("change", t, { once: !0 }), () => n.removeEventListener("change", t);
	}
}, hc = (e = []) => (t, n) => {
	H(e) && (e = [e]);
	let r = !1, i = (e) => {
		r || (r = !0, a(), t(), e.target.dispatchEvent(new e.constructor(e.type, e)));
	}, a = () => {
		n((t) => {
			for (let n of e) t.removeEventListener(n, i);
		});
	};
	return n((t) => {
		for (let n of e) t.addEventListener(n, i, { once: !0 });
	}), a;
};
function gc(e, t) {
	if (Gs(e) && e.data === "[") {
		let n = 1, r = e.nextSibling;
		for (; r;) {
			if (r.nodeType === 1) {
				if (t(r) === !1) break;
			} else if (Gs(r)) {
				if (r.data === "]") {
					if (--n === 0) break;
				} else r.data === "[" && n++;
			}
			r = r.nextSibling;
		}
	} else t(e);
}
var _c = (e) => !!e.type.__asyncLoader;
// @__NO_SIDE_EFFECTS__
function vc(e) {
	V(e) && (e = { loader: e });
	let { loader: t, loadingComponent: n, errorComponent: r, delay: i = 200, hydrate: a, timeout: o, suspensible: s = !0, onError: c } = e, l = null, u, d = 0, f = () => (d++, l = null, p()), p = () => {
		let e;
		return l || (e = l = t().catch((e) => {
			if (e = e instanceof Error ? e : Error(String(e)), c) return new Promise((t, n) => {
				c(e, () => t(f()), () => n(e), d + 1);
			});
			throw e;
		}).then((t) => e !== l && l ? l : (t && (t.__esModule || t[Symbol.toStringTag] === "Module") && (t = t.default), u = t, t)));
	};
	return /* @__PURE__ */ Ms({
		name: "AsyncComponentWrapper",
		__asyncLoader: p,
		__asyncHydrate(e, t, n) {
			let r = e.isConnected, i = !1;
			(t.bu || (t.bu = [])).push(() => i = !0);
			let o = () => {
				i || !e.parentNode || r && !e.isConnected || n();
			}, s = a ? () => {
				let n = a(o, (t) => gc(e, t));
				n && (t.bum || (t.bum = [])).push(n);
			} : o;
			u ? s() : p().then(() => !t.isUnmounted && s());
		},
		get __asyncResolved() {
			return u;
		},
		setup() {
			let e = yd;
			if (Ps(e), u) return () => yc(u, e);
			let t = (t) => {
				l = null, io(t, e, 13, !r);
			};
			if (s && e.suspense || Ed) return p().then((t) => () => yc(t, e)).catch((e) => (t(e), () => r ? $(r, { error: e }) : null));
			let a = /* @__PURE__ */ ga(!1), c = /* @__PURE__ */ ga(), d = /* @__PURE__ */ ga(!!i), f, m;
			return Ic(() => {
				f != null && clearTimeout(f), m != null && clearTimeout(m);
			}), i && (m = setTimeout(() => {
				e.isUnmounted || (d.value = !1);
			}, i)), o != null && (f = setTimeout(() => {
				if (!e.isUnmounted && !a.value && !c.value) {
					let e = /* @__PURE__ */ Error(`Async component timed out after ${o}ms.`);
					t(e), c.value = e;
				}
			}, o)), p().then(() => {
				e.isUnmounted || (a.value = !0, e.parent && bc(e.parent.vnode) && e.parent.update());
			}).catch((n) => {
				e.isUnmounted ? l = null : (t(n), c.value = n);
			}), () => {
				if (a.value && u) return yc(u, e);
				if (c.value && r) return $(r, { error: c.value });
				if (n && !d.value) return yc(n, e);
			};
		}
	});
}
function yc(e, t) {
	let { ref: n, props: r, children: i, ce: a } = t.vnode, o = $(e, r, i);
	return o.ref = n, o.ce = a, delete t.vnode.ce, o;
}
var bc = (e) => e.type.__isKeepAlive, xc = {
	name: "KeepAlive",
	__isKeepAlive: !0,
	props: {
		include: [
			String,
			RegExp,
			Array
		],
		exclude: [
			String,
			RegExp,
			Array
		],
		max: [String, Number]
	},
	setup(e, { slots: t }) {
		let n = bd(), r = n.ctx;
		if (!r.renderer) return () => {
			let e = t.default && t.default();
			return e && e.length === 1 ? e[0] : e;
		};
		let i = /* @__PURE__ */ new Map(), a = /* @__PURE__ */ new Set(), o = null;
		__VUE_PROD_DEVTOOLS__ && (n.__v_cache = i);
		let s = n.suspense, { renderer: { p: c, m: l, um: u, o: { createElement: d } } } = r, f = d("div");
		r.activate = (e, t, n, r, i) => {
			let a = e.component;
			l(e, t, n, 0, s), c(a.vnode, e, t, n, a, s, r, e.slotScopeIds, i), X(() => {
				a.isDeactivated = !1, a.a && or(a.a);
				let t = e.props && e.props.onVnodeMounted;
				t && hd(t, a.parent, e);
			}, s), __VUE_PROD_DEVTOOLS__ && Ao(a);
		}, r.deactivate = (e) => {
			let t = e.component;
			Du(t.m), Du(t.a), l(e, f, null, 1, s), X(() => {
				t.da && or(t.da);
				let n = e.props && e.props.onVnodeUnmounted;
				n && hd(n, t.parent, e), t.isDeactivated = !0;
			}, s), __VUE_PROD_DEVTOOLS__ && Ao(t);
		};
		function p(e) {
			Dc(e), u(e, n, s, !0);
		}
		function m(e) {
			i.forEach((t, n) => {
				let r = Bd(_c(t) ? t.type.__asyncResolved || {} : t.type);
				r && !e(r) && h(n);
			});
		}
		function h(e) {
			let t = i.get(e);
			t && (!o || !ed(t, o)) ? p(t) : o && Dc(o), i.delete(e), a.delete(e);
		}
		$o(() => [e.include, e.exclude], ([e, t]) => {
			e && m((t) => Sc(e, t)), t && m((e) => !Sc(t, e));
		}, {
			flush: "post",
			deep: !0
		});
		let g = null, _ = () => {
			g != null && (ku(n.subTree.type) ? X(() => {
				let e = Oc(n.subTree);
				e.component && i.set(g, e);
			}, n.subTree.suspense) : i.set(g, Oc(n.subTree)));
		};
		return Mc(_), Pc(_), Fc(() => {
			i.forEach((e) => {
				let { subTree: t, suspense: r } = n, i = Oc(t);
				if (e.type === i.type && e.key === i.key) {
					Dc(i);
					let e = i.component.da;
					e && X(e, r);
				} else p(e);
			});
		}), () => {
			if (g = null, !t.default) return o = null;
			let n = t.default(), r = n[0];
			if (n.length > 1) return o = null, n;
			if (!$u(r) || !(r.shapeFlag & 4) && !(r.shapeFlag & 128)) return o = null, r;
			let s = Oc(r);
			if (s.type === Q) return o = null, s;
			let c = s.type, l = Bd(_c(s) ? s.type.__asyncResolved || {} : c), { include: u, exclude: d, max: f } = e;
			if (u && (!l || !Sc(u, l)) || d && l && Sc(d, l)) return s.shapeFlag &= -257, o = s, r;
			let p = s.key == null ? c : s.key, m = i.get(p);
			return s.el && (s = sd(s), r.shapeFlag & 128 && (r.ssContent = s)), g = p, m ? (s.el = m.el, s.component = m.component, s.transition && As(s, s.transition), s.shapeFlag |= 512, a.delete(p), a.add(p)) : (a.add(p), f && a.size > parseInt(f, 10) && h(a.values().next().value)), s.shapeFlag |= 256, o = s, ku(r.type) ? r : s;
		};
	}
};
function Sc(e, t) {
	return B(e) ? e.some((e) => Sc(e, t)) : H(e) ? e.split(",").includes(t) : Gn(e) ? (e.lastIndex = 0, e.test(t)) : !1;
}
function Cc(e, t) {
	Tc(e, "a", t);
}
function wc(e, t) {
	Tc(e, "da", t);
}
function Tc(e, t, n = yd) {
	let r = e.__wdc || (e.__wdc = () => {
		let t = n;
		for (; t;) {
			if (t.isDeactivated) return;
			t = t.parent;
		}
		return e();
	});
	if (kc(t, r, n), n) {
		let e = n.parent;
		for (; e && e.parent;) bc(e.parent.vnode) && Ec(r, t, n, e), e = e.parent;
	}
}
function Ec(e, t, n, r) {
	let i = kc(t, e, r, !0);
	Ic(() => {
		Bn(r[t], i);
	}, n);
}
function Dc(e) {
	e.shapeFlag &= -257, e.shapeFlag &= -513;
}
function Oc(e) {
	return e.shapeFlag & 128 ? e.ssContent : e;
}
function kc(e, t, n = yd, r = !1) {
	if (n) {
		let i = n[e] || (n[e] = []), a = t.__weh || (t.__weh = (...r) => {
			li();
			let i = Cd(n), a = ro(t, n, e, r);
			return i(), ui(), a;
		});
		return r ? i.unshift(a) : i.push(a), a;
	}
}
var Ac = (e) => (t, n = yd) => {
	(!Ed || e === "sp") && kc(e, (...e) => t(...e), n);
}, jc = Ac("bm"), Mc = Ac("m"), Nc = Ac("bu"), Pc = Ac("u"), Fc = Ac("bum"), Ic = Ac("um"), Lc = Ac("sp"), Rc = Ac("rtg"), zc = Ac("rtc");
function Bc(e, t = yd) {
	kc("ec", e, t);
}
var Vc = "components", Hc = "directives";
function Uc(e, t) {
	return qc(Vc, e, !0, t) || e;
}
var Wc = /* @__PURE__ */ Symbol.for("v-ndc");
function Gc(e) {
	return H(e) ? qc(Vc, e, !1) || e : e || Wc;
}
function Kc(e) {
	return qc(Hc, e);
}
function qc(e, t, n = !0, r = !1) {
	let i = Io || yd;
	if (i) {
		let n = i.type;
		if (e === Vc) {
			let e = Bd(n, !1);
			if (e && (e === t || e === W(t) || e === ir(W(t)))) return n;
		}
		let a = Jc(i[e] || n[e], t) || Jc(i.appContext[e], t);
		return !a && r ? n : a;
	}
}
function Jc(e, t) {
	return e && (e[t] || e[W(t)] || e[ir(W(t))]);
}
function Yc(e, t, n, r) {
	let i, a = n && n[r], o = B(e);
	if (o || H(e)) {
		let n = o && /* @__PURE__ */ la(e), r = !1, s = !1;
		n && (r = !/* @__PURE__ */ da(e), s = /* @__PURE__ */ ua(e), e = wi(e)), i = Array(e.length);
		for (let n = 0, o = e.length; n < o; n++) i[n] = t(r ? s ? ha(ma(e[n])) : ma(e[n]) : e[n], n, void 0, a && a[n]);
	} else if (typeof e == "number") {
		i = Array(e);
		for (let n = 0; n < e; n++) i[n] = t(n + 1, n, void 0, a && a[n]);
	} else if (U(e)) {
		if (e[Symbol.iterator]) i = Array.from(e, (e, n) => t(e, n, void 0, a && a[n]));
		else {
			let n = Object.keys(e);
			i = Array(n.length);
			for (let r = 0, o = n.length; r < o; r++) {
				let o = n[r];
				i[r] = t(e[o], o, r, a && a[r]);
			}
		}
	} else i = [];
	return n && (n[r] = i), i;
}
function Xc(e, t) {
	for (let n = 0; n < t.length; n++) {
		let r = t[n];
		if (B(r)) for (let t = 0; t < r.length; t++) e[r[t].name] = r[t].fn;
		else r && (e[r.name] = r.key ? (...e) => {
			let t = r.fn(...e);
			return t && (t.key = r.key), t;
		} : r.fn);
	}
	return e;
}
function Zc(e, t, n, r, i, a) {
	if (n ?? (n = {}), Io.ce || Io.parent && _c(Io.parent) && Io.parent.ce) {
		let e = a != null && n.key == null ? R({}, n, { key: a }) : n, i = Object.keys(e).length > 0;
		return t !== "default" && (e.name = t), Ku(), Qu(Z, null, [$("slot", e, r && r())], i ? -2 : 64);
	}
	let o = e[t];
	o && o._c && (o._d = !1);
	let s = Wu.length;
	Ku();
	let c;
	try {
		let i = o && Qc(o(n)), s = n.key || a || i && i.key;
		c = Qu(Z, { key: (s && !Kn(s) ? s : `_${t}`) + (!i && r ? "_fb" : "") }, i || (r ? r() : []), i && e._ === 1 ? 64 : -2);
	} catch (e) {
		for (let e = Wu.length; e > s; e--) qu();
		throw e;
	} finally {
		o && o._c && (o._d = !0);
	}
	return !i && c.scopeId && (c.slotScopeIds = [c.scopeId + "-s"]), c;
}
function Qc(e) {
	return e.some((e) => !$u(e) || !(e.type === Q || e.type === Z && !Qc(e.children))) ? e : null;
}
function $c(e, t) {
	let n = {};
	for (let r in e) n[t && /[A-Z]/.test(r) ? `on:${r}` : ar(r)] = e[r];
	return n;
}
var el = (e) => e ? Td(e) ? Ld(e) : el(e.parent) : null, tl = /* @__PURE__ */ R(/* @__PURE__ */ Object.create(null), {
	$: (e) => e,
	$el: (e) => e.vnode.el,
	$data: (e) => e.data,
	$props: (e) => e.props,
	$attrs: (e) => e.attrs,
	$slots: (e) => e.slots,
	$refs: (e) => e.refs,
	$parent: (e) => el(e.parent),
	$root: (e) => el(e.root),
	$host: (e) => e.ce,
	$emit: (e) => e.emit,
	$options: (e) => __VUE_OPTIONS_API__ ? Tl(e) : e.type,
	$forceUpdate: (e) => e.f || (e.f = () => {
		go(e.update);
	}),
	$nextTick: (e) => e.n || (e.n = mo.bind(e.proxy)),
	$watch: (e) => __VUE_OPTIONS_API__ ? ts.bind(e) : In
}), nl = (e, t) => e !== L && !e.__isScriptSetup && z(e, t), rl = {
	get({ _: e }, t) {
		if (t === "__v_skip") return !0;
		let { ctx: n, setupState: r, data: i, props: a, accessCache: o, type: s, appContext: c } = e;
		if (t[0] !== "$") {
			let e = o[t];
			if (e !== void 0) switch (e) {
				case 1: return r[t];
				case 2: return i[t];
				case 4: return n[t];
				case 3: return a[t];
			}
			else if (nl(r, t)) return o[t] = 1, r[t];
			else if (__VUE_OPTIONS_API__ && i !== L && z(i, t)) return o[t] = 2, i[t];
			else if (z(a, t)) return o[t] = 3, a[t];
			else if (n !== L && z(n, t)) return o[t] = 4, n[t];
			else (!__VUE_OPTIONS_API__ || bl) && (o[t] = 0);
		}
		let l = tl[t], u, d;
		if (l) return t === "$attrs" && bi(e.attrs, "get", ""), l(e);
		if ((u = s.__cssModules) && (u = u[t])) return u;
		if (n !== L && z(n, t)) return o[t] = 4, n[t];
		if (d = c.config.globalProperties, z(d, t)) return d[t];
	},
	set({ _: e }, t, n) {
		let { data: r, setupState: i, ctx: a } = e;
		return nl(i, t) ? (i[t] = n, !0) : __VUE_OPTIONS_API__ && r !== L && z(r, t) ? (r[t] = n, !0) : z(e.props, t) || t[0] === "$" && t.slice(1) in e ? !1 : (a[t] = n, !0);
	},
	has({ _: { data: e, setupState: t, accessCache: n, ctx: r, appContext: i, props: a, type: o } }, s) {
		let c;
		return !!(n[s] || __VUE_OPTIONS_API__ && e !== L && s[0] !== "$" && z(e, s) || nl(t, s) || z(a, s) || z(r, s) || z(tl, s) || z(i.config.globalProperties, s) || (c = o.__cssModules) && c[s]);
	},
	defineProperty(e, t, n) {
		return n.get == null ? z(n, "value") && this.set(e, t, n.value, null) : e._.accessCache[t] = 0, Reflect.defineProperty(e, t, n);
	}
}, il = /* @__PURE__ */ R({}, rl, {
	get(e, t) {
		if (t !== Symbol.unscopables) return rl.get(e, t, e);
	},
	has(e, t) {
		return t[0] !== "_" && !fr(t);
	}
});
function al() {
	return null;
}
function ol() {
	return null;
}
function sl(e) {}
function cl(e) {}
function ll() {
	return null;
}
function ul() {}
function dl(e, t) {
	return null;
}
function fl() {
	return ml("useSlots").slots;
}
function pl() {
	return ml("useAttrs").attrs;
}
function ml(e) {
	let t = bd();
	return t.setupContext || (t.setupContext = Id(t));
}
function hl(e) {
	return B(e) ? e.reduce((e, t) => (e[t] = null, e), {}) : e;
}
function gl(e, t) {
	let n = hl(e);
	for (let e in t) {
		if (e.startsWith("__skip")) continue;
		let r = n[e];
		r ? B(r) || V(r) ? r = n[e] = {
			type: r,
			default: t[e]
		} : r.default = t[e] : r === null && (r = n[e] = { default: t[e] }), r && t[`__skip_${e}`] && (r.skipFactory = !0);
	}
	return n;
}
function _l(e, t) {
	return !e || !t ? e || t : B(e) && B(t) ? e.concat(t) : R({}, hl(e), hl(t));
}
function vl(e, t) {
	let n = {};
	for (let r in e) t.includes(r) || Object.defineProperty(n, r, {
		enumerable: !0,
		get: () => e[r]
	});
	return n;
}
function yl(e) {
	let t = bd(), n = Ed, r = e();
	wd(), n && Sd(!1);
	let i = () => {
		Cd(t), n && Sd(!0);
	}, a = () => {
		bd() !== t && t.scope.off(), wd(), n && Sd(!1);
	};
	return qn(r) && (r = r.catch((e) => {
		throw i(), Promise.resolve().then(() => Promise.resolve().then(a)), e;
	})), [r, () => {
		i(), Promise.resolve().then(a);
	}];
}
var bl = !0;
function xl(e) {
	let t = Tl(e), n = e.proxy, r = e.ctx;
	bl = !1, t.beforeCreate && Cl(t.beforeCreate, e, "bc");
	let { data: i, computed: a, methods: o, watch: s, provide: c, inject: l, created: u, beforeMount: d, mounted: f, beforeUpdate: p, updated: m, activated: h, deactivated: g, beforeDestroy: _, beforeUnmount: v, destroyed: y, unmounted: b, render: x, renderTracked: S, renderTriggered: C, errorCaptured: w, serverPrefetch: T, expose: E, inheritAttrs: D, components: ee, directives: O, filters: k } = t;
	if (l && Sl(l, r, null), o) for (let e in o) {
		let t = o[e];
		V(t) && (r[e] = t.bind(n));
	}
	if (i) {
		let t = i.call(n, n);
		U(t) && (e.data = /* @__PURE__ */ ia(t));
	}
	if (bl = !0, a) for (let e in a) {
		let t = a[e], i = Ud({
			get: V(t) ? t.bind(n, n) : V(t.get) ? t.get.bind(n, n) : In,
			set: !V(t) && V(t.set) ? t.set.bind(n) : In
		});
		Object.defineProperty(r, e, {
			enumerable: !0,
			configurable: !0,
			get: () => i.value,
			set: (e) => i.value = e
		});
	}
	if (s) for (let e in s) wl(s[e], r, n, e);
	if (c) {
		let e = V(c) ? c.call(n) : c;
		Reflect.ownKeys(e).forEach((t) => {
			Go(t, e[t]);
		});
	}
	u && Cl(u, e, "c");
	function A(e, t) {
		B(t) ? t.forEach((t) => e(t.bind(n))) : t && e(t.bind(n));
	}
	if (A(jc, d), A(Mc, f), A(Nc, p), A(Pc, m), A(Cc, h), A(wc, g), A(Bc, w), A(zc, S), A(Rc, C), A(Fc, v), A(Ic, b), A(Lc, T), B(E)) {
		if (E.length) {
			let t = e.exposed || (e.exposed = {});
			E.forEach((e) => {
				Object.defineProperty(t, e, {
					get: () => n[e],
					set: (t) => n[e] = t,
					enumerable: !0
				});
			});
		} else e.exposed || (e.exposed = {});
	}
	x && e.render === In && (e.render = x), D != null && (e.inheritAttrs = D), ee && (e.components = ee), O && (e.directives = O), T && Ps(e);
}
function Sl(e, t, n = In) {
	B(e) && (e = Al(e));
	for (let n in e) {
		let r = e[n], i;
		i = U(r) ? "default" in r ? Ko(r.from || n, r.default, !0) : Ko(r.from || n) : Ko(r), /* @__PURE__ */ Y(i) ? Object.defineProperty(t, n, {
			enumerable: !0,
			configurable: !0,
			get: () => i.value,
			set: (e) => i.value = e
		}) : t[n] = i;
	}
}
function Cl(e, t, n) {
	ro(B(e) ? e.map((e) => e.bind(t.proxy)) : e.bind(t.proxy), t, n);
}
function wl(e, t, n, r) {
	let i = r.includes(".") ? ns(n, r) : () => n[r];
	if (H(e)) {
		let n = t[e];
		V(n) && $o(i, n);
	} else if (V(e)) $o(i, e.bind(n));
	else if (U(e)) {
		if (B(e)) e.forEach((e) => wl(e, t, n, r));
		else {
			let r = V(e.handler) ? e.handler.bind(n) : t[e.handler];
			V(r) && $o(i, r, e);
		}
	}
}
function Tl(e) {
	let t = e.type, { mixins: n, extends: r } = t, { mixins: i, optionsCache: a, config: { optionMergeStrategies: o } } = e.appContext, s = a.get(t), c;
	return s ? c = s : !i.length && !n && !r ? c = t : (c = {}, i.length && i.forEach((e) => El(c, e, o, !0)), El(c, t, o)), U(t) && a.set(t, c), c;
}
function El(e, t, n, r = !1) {
	let { mixins: i, extends: a } = t;
	a && El(e, a, n, !0), i && i.forEach((t) => El(e, t, n, !0));
	for (let i in t) if (!(r && i === "expose")) {
		let r = Dl[i] || n && n[i];
		e[i] = r ? r(e[i], t[i]) : t[i];
	}
	return e;
}
var Dl = {
	data: Ol,
	props: Nl,
	emits: Nl,
	methods: Ml,
	computed: Ml,
	beforeCreate: jl,
	created: jl,
	beforeMount: jl,
	mounted: jl,
	beforeUpdate: jl,
	updated: jl,
	beforeDestroy: jl,
	beforeUnmount: jl,
	destroyed: jl,
	unmounted: jl,
	activated: jl,
	deactivated: jl,
	errorCaptured: jl,
	serverPrefetch: jl,
	components: Ml,
	directives: Ml,
	watch: Pl,
	provide: Ol,
	inject: kl
};
function Ol(e, t) {
	return t ? e ? function() {
		return R(V(e) ? e.call(this, this) : e, V(t) ? t.call(this, this) : t);
	} : t : e;
}
function kl(e, t) {
	return Ml(Al(e), Al(t));
}
function Al(e) {
	if (B(e)) {
		let t = {};
		for (let n = 0; n < e.length; n++) t[e[n]] = e[n];
		return t;
	}
	return e;
}
function jl(e, t) {
	return e ? [...new Set([].concat(e, t))] : t;
}
function Ml(e, t) {
	return e ? R(/* @__PURE__ */ Object.create(null), e, t) : t;
}
function Nl(e, t) {
	return e ? B(e) && B(t) ? [.../* @__PURE__ */ new Set([...e, ...t])] : R(/* @__PURE__ */ Object.create(null), hl(e), hl(t ?? {})) : t;
}
function Pl(e, t) {
	if (!e) return t;
	if (!t) return e;
	let n = R(/* @__PURE__ */ Object.create(null), e);
	for (let r in t) n[r] = jl(e[r], t[r]);
	return n;
}
function Fl() {
	return {
		app: null,
		config: {
			isNativeTag: Ln,
			performance: !1,
			globalProperties: {},
			optionMergeStrategies: {},
			errorHandler: void 0,
			warnHandler: void 0,
			compilerOptions: {}
		},
		mixins: [],
		components: {},
		directives: {},
		provides: /* @__PURE__ */ Object.create(null),
		optionsCache: /* @__PURE__ */ new WeakMap(),
		propsCache: /* @__PURE__ */ new WeakMap(),
		emitsCache: /* @__PURE__ */ new WeakMap()
	};
}
var Il = 0;
function Ll(e, t) {
	return function(n, r = null) {
		V(n) || (n = R({}, n)), r != null && !U(r) && (r = null);
		let i = Fl(), a = /* @__PURE__ */ new WeakSet(), o = [], s = !1, c = i.app = {
			_uid: Il++,
			_component: n,
			_props: r,
			_container: null,
			_context: i,
			_instance: null,
			version: Jd,
			get config() {
				return i.config;
			},
			set config(e) {},
			use(e, ...t) {
				return a.has(e) || (e && V(e.install) ? (a.add(e), e.install(c, ...t)) : V(e) && (a.add(e), e(c, ...t))), c;
			},
			mixin(e) {
				return __VUE_OPTIONS_API__ && (i.mixins.includes(e) || i.mixins.push(e)), c;
			},
			component(e, t) {
				return t ? (i.components[e] = t, c) : i.components[e];
			},
			directive(e, t) {
				return t ? (i.directives[e] = t, c) : i.directives[e];
			},
			mount(a, o, l) {
				if (!s) {
					let u = c._ceVNode || $(n, r);
					return u.appContext = i, l === !0 ? l = "svg" : l === !1 && (l = void 0), o && t ? t(u, a) : e(u, a, l), s = !0, c._container = a, a.__vue_app__ = c, __VUE_PROD_DEVTOOLS__ && (c._instance = u.component, Oo(c, Jd)), Ld(u.component);
				}
			},
			onUnmount(e) {
				o.push(e);
			},
			unmount() {
				s && (ro(o, c._instance, 16), e(null, c._container), __VUE_PROD_DEVTOOLS__ && (c._instance = null, ko(c)), delete c._container.__vue_app__);
			},
			provide(e, t) {
				return i.provides[e] = t, c;
			},
			runWithContext(e) {
				let t = Rl;
				Rl = c;
				try {
					return e();
				} finally {
					Rl = t;
				}
			}
		};
		return c;
	};
}
var Rl = null;
function zl(e, t, n = L) {
	let r = bd(), i = W(t), a = rr(t), o = Bl(e, i), s = Ea((o, s) => {
		let c, l = L, u;
		return Qo(() => {
			let t = e[i];
			G(c, t) && (c = t, s());
		}), {
			get() {
				return o(), n.get ? n.get(c) : c;
			},
			set(e) {
				let o = n.set ? n.set(e) : e;
				if (!G(o, c) && !(l !== L && G(e, l))) return;
				let d = r.vnode.props, f = !!(d && (t in d || i in d || a in d) && (`onUpdate:${t}` in d || `onUpdate:${i}` in d || `onUpdate:${a}` in d));
				f || (c = e, s()), r.emit(`update:${t}`, o), G(e, l) && (G(e, o) && !G(o, u) || f && l !== L && !G(o, c)) && s(), l = e, u = o;
			}
		};
	});
	return s[Symbol.iterator] = () => {
		let e = 0;
		return { next() {
			return e < 2 ? {
				value: e++ ? o || L : s,
				done: !1
			} : { done: !0 };
		} };
	}, s;
}
var Bl = (e, t) => t === "modelValue" || t === "model-value" ? e.modelModifiers : e[`${t}Modifiers`] || e[`${W(t)}Modifiers`] || e[`${rr(t)}Modifiers`];
function Vl(e, t, ...n) {
	if (e.isUnmounted) return;
	let r = e.vnode.props || L, i = n, a = t.startsWith("update:"), o = a && Bl(r, t.slice(7));
	o && (o.trim && (i = n.map((e) => H(e) ? e.trim() : e)), o.number && (i = i.map(cr))), __VUE_PROD_DEVTOOLS__ && Fo(e, t, i);
	let s, c = r[s = ar(t)] || r[s = ar(W(t))];
	!c && a && (c = r[s = ar(rr(t))]), c && ro(c, e, 6, i);
	let l = r[s + "Once"];
	if (l) {
		if (!e.emitted) e.emitted = {};
		else if (e.emitted[s]) return;
		e.emitted[s] = !0, ro(l, e, 6, i);
	}
}
var Hl = /* @__PURE__ */ new WeakMap();
function Ul(e, t, n = !1) {
	let r = __VUE_OPTIONS_API__ && n ? Hl : t.emitsCache, i = r.get(e);
	if (i !== void 0) return i;
	let a = e.emits, o = {}, s = !1;
	if (__VUE_OPTIONS_API__ && !V(e)) {
		let r = (e) => {
			let n = Ul(e, t, !0);
			n && (s = !0, R(o, n));
		};
		!n && t.mixins.length && t.mixins.forEach(r), e.extends && r(e.extends), e.mixins && e.mixins.forEach(r);
	}
	return !a && !s ? (U(e) && r.set(e, null), null) : (B(a) ? a.forEach((e) => o[e] = null) : R(o, a), U(e) && r.set(e, o), o);
}
function Wl(e, t) {
	return !e || !Rn(t) ? !1 : (t = t.slice(2), t = t === "Once" ? t : t.replace(/Once$/, ""), z(e, t[0].toLowerCase() + t.slice(1)) || z(e, rr(t)) || z(e, t));
}
function Gl(e) {
	let { type: t, vnode: n, proxy: r, withProxy: i, propsOptions: [a], slots: o, attrs: s, emit: c, render: l, renderCache: u, props: d, data: f, setupState: p, ctx: m, inheritAttrs: h } = e, g = Ro(e), _, v;
	try {
		if (n.shapeFlag & 4) {
			let e = i || r, t = e;
			_ = dd(l.call(t, e, u, d, p, f, m)), v = s;
		} else {
			let e = t;
			_ = dd(e.length > 1 ? e(d, {
				attrs: s,
				slots: o,
				emit: c
			}) : e(d, null)), v = t.props ? s : ql(s);
		}
	} catch (t) {
		Wu.length = 0, io(t, e, 1), _ = $(Q);
	}
	let y = _;
	if (v && h !== !1) {
		let e = Object.keys(v), { shapeFlag: t } = y;
		e.length && t & 7 && (a && e.some(zn) && (v = Jl(v, a)), y = sd(y, v, !1, !0));
	}
	return n.dirs && (y = sd(y, null, !1, !0), y.dirs = y.dirs ? y.dirs.concat(n.dirs) : n.dirs), n.transition && As(as(y.type) && ks(y) || y, n.transition), _ = y, Ro(g), _;
}
function Kl(e, t = !0) {
	let n;
	for (let t = 0; t < e.length; t++) {
		let r = e[t];
		if ($u(r)) {
			if (r.type !== Q || r.children === "v-if") {
				if (n) return;
				n = r;
			}
		} else return;
	}
	return n;
}
var ql = (e) => {
	let t;
	for (let n in e) (n === "class" || n === "style" || Rn(n)) && ((t || (t = {}))[n] = e[n]);
	return t;
}, Jl = (e, t) => {
	let n = {};
	for (let r in e) (!zn(r) || !(r.slice(9) in t)) && (n[r] = e[r]);
	return n;
};
function Yl(e, t, n) {
	let { props: r, children: i, component: a } = e, { props: o, children: s, patchFlag: c } = t, l = a.emitsOptions;
	if (t.dirs || t.transition) return !0;
	if (n && c >= 0) {
		if (c & 1024) return !0;
		if (c & 16) return r ? Xl(r, o, l) : !!o;
		if (c & 8) {
			let e = t.dynamicProps;
			for (let t = 0; t < e.length; t++) {
				let n = e[t];
				if (Zl(o, r, n) && !Wl(l, n)) return !0;
			}
		}
	} else return (i || s) && (!s || !s.$stable) ? !0 : r === o ? !1 : r ? !o || Xl(r, o, l) : !!o;
	return !1;
}
function Xl(e, t, n) {
	let r = Object.keys(t);
	if (r.length !== Object.keys(e).length) return !0;
	for (let i = 0; i < r.length; i++) {
		let a = r[i];
		if (Zl(t, e, a) && !Wl(n, a)) return !0;
	}
	return !1;
}
function Zl(e, t, n) {
	let r = e[n], i = t[n];
	return n === "style" && U(r) && U(i) ? !Pr(r, i) : r !== i;
}
function Ql({ vnode: e, parent: t, suspense: n }, r) {
	for (; t;) {
		let n = t.subTree;
		if (n.suspense && n.suspense.activeBranch === e && (n.suspense.vnode.el = n.el = r, e = n), n === e) (e = t.vnode).el = r, t = t.parent;
		else break;
	}
	n && n.activeBranch === e && (n.vnode.el = r);
}
var $l = {}, eu = () => Object.create($l), tu = (e) => Object.getPrototypeOf(e) === $l;
function nu(e, t, n, r = !1) {
	let i = {}, a = eu();
	e.propsDefaults = /* @__PURE__ */ Object.create(null), iu(e, t, i, a);
	for (let t in e.propsOptions[0]) t in i || (i[t] = void 0);
	e.props = n ? r ? i : /* @__PURE__ */ aa(i) : e.type.props ? i : a, e.attrs = a;
}
function ru(e, t, n, r) {
	let { props: i, attrs: a, vnode: { patchFlag: o } } = e, s = /* @__PURE__ */ J(i), [c] = e.propsOptions, l = !1;
	if ((r || o > 0) && !(o & 16)) {
		if (o & 8) {
			let n = e.vnode.dynamicProps;
			for (let r = 0; r < n.length; r++) {
				let o = n[r];
				if (Wl(e.emitsOptions, o)) continue;
				let u = t[o];
				if (c) {
					if (z(a, o)) u !== a[o] && (a[o] = u, l = !0);
					else {
						let t = W(o);
						i[t] = au(c, s, t, u, e, !1);
					}
				} else u !== a[o] && (a[o] = u, l = !0);
			}
		}
	} else {
		iu(e, t, i, a) && (l = !0);
		let r;
		for (let a in s) (!t || !z(t, a) && ((r = rr(a)) === a || !z(t, r))) && (c ? n && (n[a] !== void 0 || n[r] !== void 0) && (i[a] = au(c, s, a, void 0, e, !0)) : delete i[a]);
		if (a !== s) for (let e in a) (!t || !z(t, e)) && (delete a[e], l = !0);
	}
	l && xi(e.attrs, "set", "");
}
function iu(e, t, n, r) {
	let [i, a] = e.propsOptions, o = !1, s;
	if (t) for (let c in t) {
		if ($n(c)) continue;
		let l = t[c], u;
		i && z(i, u = W(c)) ? !a || !a.includes(u) ? n[u] = l : (s || (s = {}))[u] = l : Wl(e.emitsOptions, c) || (!(c in r) || l !== r[c]) && (r[c] = l, o = !0);
	}
	if (a) {
		let t = /* @__PURE__ */ J(n), r = s || L;
		for (let o = 0; o < a.length; o++) {
			let s = a[o];
			n[s] = au(i, t, s, r[s], e, !z(r, s));
		}
	}
	return o;
}
function au(e, t, n, r, i, a) {
	let o = e[n];
	if (o != null) {
		let e = z(o, "default");
		if (e && r === void 0) {
			let e = o.default;
			if (o.type !== Function && !o.skipFactory && V(e)) {
				let { propsDefaults: a } = i;
				if (n in a) r = a[n];
				else {
					let o = Cd(i);
					r = a[n] = e.call(null, t), o();
				}
			} else r = e;
			i.ce && i.ce._setProp(n, r);
		}
		o[0] && (a && !e ? r = !1 : o[1] && (r === "" || r === rr(n)) && (r = !0));
	}
	return r;
}
var ou = /* @__PURE__ */ new WeakMap();
function su(e, t, n = !1) {
	let r = __VUE_OPTIONS_API__ && n ? ou : t.propsCache, i = r.get(e);
	if (i) return i;
	let a = e.props, o = {}, s = [], c = !1;
	if (__VUE_OPTIONS_API__ && !V(e)) {
		let r = (e) => {
			c = !0;
			let [n, r] = su(e, t, !0);
			R(o, n), r && s.push(...r);
		};
		!n && t.mixins.length && t.mixins.forEach(r), e.extends && r(e.extends), e.mixins && e.mixins.forEach(r);
	}
	if (!a && !c) return U(e) && r.set(e, Fn), Fn;
	if (B(a)) for (let e = 0; e < a.length; e++) {
		let t = W(a[e]);
		cu(t) && (o[t] = L);
	}
	else if (a) for (let e in a) {
		let t = W(e);
		if (cu(t)) {
			let n = a[e], r = o[t] = B(n) || V(n) ? { type: n } : R({}, n), i = r.type, c = !1, l = !0;
			if (B(i)) for (let e = 0; e < i.length; ++e) {
				let t = i[e], n = V(t) && t.name;
				if (n === "Boolean") {
					c = !0;
					break;
				}
				n === "String" && (l = !1);
			}
			else c = V(i) && i.name === "Boolean";
			r[0] = c, r[1] = l, (c || z(r, "default")) && s.push(t);
		}
	}
	let l = [o, s];
	return U(e) && r.set(e, l), l;
}
function cu(e) {
	return e[0] !== "$" && !$n(e);
}
var lu = (e) => e === "_" || e === "_ctx" || e === "$stable", uu = (e) => B(e) ? e.map(dd) : [dd(e)], du = (e, t, n) => {
	if (t._n) return t;
	let r = Ho((...e) => uu(t(...e)), n);
	return r._c = !1, r;
}, fu = (e, t, n) => {
	let r = e._ctx;
	for (let n in e) {
		if (lu(n)) continue;
		let i = e[n];
		if (V(i)) t[n] = du(n, i, r);
		else if (i != null) {
			let e = uu(i);
			t[n] = () => e;
		}
	}
}, pu = (e, t) => {
	let n = uu(t);
	e.slots.default = () => n;
}, mu = (e, t, n) => {
	for (let r in t) (n || !lu(r)) && (e[r] = t[r]);
}, hu = (e, t, n) => {
	let r = e.slots = eu();
	if (e.vnode.shapeFlag & 32) {
		let e = t._;
		e ? (mu(r, t, n), n && sr(r, "_", e, !0)) : fu(t, r);
	} else t && pu(e, t);
}, gu = (e, t, n) => {
	let { vnode: r, slots: i } = e, a = !0, o = L;
	if (r.shapeFlag & 32) {
		let e = t._;
		e ? n && e === 1 ? a = !1 : mu(i, t, n) : (a = !t.$stable, fu(t, i)), o = t;
	} else t && (pu(e, t), o = { default: 1 });
	if (a) for (let e in i) !lu(e) && o[e] == null && delete i[e];
};
function _u() {
	typeof __VUE_OPTIONS_API__ != "boolean" && (dr().__VUE_OPTIONS_API__ = !0), typeof __VUE_PROD_DEVTOOLS__ != "boolean" && (dr().__VUE_PROD_DEVTOOLS__ = !1), typeof __VUE_PROD_HYDRATION_MISMATCH_DETAILS__ != "boolean" && (dr().__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ = !1);
}
var X = zu;
function vu(e) {
	return bu(e);
}
function yu(e) {
	return bu(e, Ks);
}
function bu(e, t) {
	_u();
	let n = dr();
	n.__VUE__ = !0, __VUE_PROD_DEVTOOLS__ && Do(n.__VUE_DEVTOOLS_GLOBAL_HOOK__, n);
	let { insert: r, remove: i, patchProp: a, createElement: o, createText: s, createComment: c, setText: l, setElementText: u, parentNode: d, nextSibling: f, setScopeId: p = In, insertStaticContent: m } = e, h = (e, t, n, r = null, i = null, a = null, o = void 0, s = null, c = !!t.dynamicChildren) => {
		if (e === t) return;
		e && !ed(e, t) && (r = ce(e), P(e, i, a, !0), e = null), t.patchFlag === -2 && (c = !1, t.dynamicChildren = null), t.dynamicChildren && e && e.dynamicChildren && e.dynamicChildren.hasOnce && (t.dynamicChildren === Fn && (t.dynamicChildren = []), t.dynamicChildren.hasOnce = !0);
		let { type: l, ref: u, shapeFlag: d } = t;
		switch (l) {
			case Hu:
				g(e, t, n, r);
				break;
			case Q:
				_(e, t, n, r);
				break;
			case Uu:
				e ?? v(t, n, r, o);
				break;
			case Z:
				ee(e, t, n, r, i, a, o, s, c);
				break;
			default: d & 1 ? x(e, t, n, r, i, a, o, s, c) : d & 6 ? O(e, t, n, r, i, a, o, s, c) : (d & 64 || d & 128) && l.process(e, t, n, r, i, a, o, s, c, F);
		}
		u != null && i ? Rs(u, e && e.ref, a, t || e, !t) : u == null && e && e.ref != null && Rs(e.ref, null, a, e, !0);
	}, g = (e, t, n, i) => {
		if (e == null) r(t.el = s(t.children), n, i);
		else {
			let n = t.el = e.el;
			t.children !== e.children && l(n, t.children);
		}
	}, _ = (e, t, n, i) => {
		e == null ? r(t.el = c(t.children || ""), n, i) : t.el = e.el;
	}, v = (e, t, n, r) => {
		[e.el, e.anchor] = m(e.children, t, n, r, e.el, e.anchor);
	}, y = ({ el: e, anchor: t }, n, i) => {
		let a;
		for (; e && e !== t;) a = f(e), r(e, n, i), e = a;
		r(t, n, i);
	}, b = ({ el: e, anchor: t }) => {
		let n;
		for (; e && e !== t;) n = f(e), i(e), e = n;
		i(t);
	}, x = (e, t, n, r, i, a, o, s, c) => {
		if (t.type === "svg" ? o = "svg" : t.type === "math" && (o = "mathml"), e == null) S(t, n, r, i, a, o, s, c);
		else {
			let n = e.el && e.el._isVueCE ? e.el : null;
			try {
				n && n._beginPatch(), T(e, t, i, a, o, s, c);
			} finally {
				n && n._endPatch();
			}
		}
	}, S = (e, t, n, i, s, c, l, d) => {
		let f, p, { props: m, shapeFlag: h, transition: g, dirs: _ } = e;
		if (f = e.el = o(e.type, c, m && m.is, m), h & 8 ? u(f, e.children) : h & 16 && w(e.children, f, null, i, s, xu(e, c), l, d), _ && Wo(e, null, i, "created"), C(f, e, e.scopeId, l, i), m) {
			for (let e in m) e !== "value" && !$n(e) && a(f, e, null, m[e], c, i);
			"value" in m && a(f, "value", null, m.value, c), (p = m.onVnodeBeforeMount) && hd(p, i, e);
		}
		__VUE_PROD_DEVTOOLS__ && (sr(f, "__vnode", e, !0), sr(f, "__vueParentComponent", i, !0)), _ && Wo(e, null, i, "beforeMount");
		let v = Cu(s, g);
		v && g.beforeEnter(f), r(f, t, n), ((p = m && m.onVnodeMounted) || v || _) && X(() => {
			try {
				p && hd(p, i, e), v && g.enter(f), _ && Wo(e, null, i, "mounted");
			} finally {}
		}, s);
	}, C = (e, t, n, r, i) => {
		if (n && p(e, n), r) for (let t = 0; t < r.length; t++) p(e, r[t]);
		if (i) {
			let n = i.subTree;
			if (t === n || ku(n.type) && (n.ssContent === t || n.ssFallback === t)) {
				let t = i.vnode;
				C(e, t, t.scopeId, t.slotScopeIds, i.parent);
			}
		}
	}, w = (e, t, n, r, i, a, o, s, c = 0) => {
		for (let l = c; l < e.length; l++) {
			let c = e[l] = s ? fd(e[l]) : dd(e[l]);
			h(null, c, t, n, r, i, a, o, s);
		}
	}, T = (e, t, n, r, i, o, s) => {
		let c = t.el = e.el;
		__VUE_PROD_DEVTOOLS__ && (c.__vnode = t);
		let { patchFlag: l, dynamicChildren: d, dirs: f } = t;
		l |= e.patchFlag & 16;
		let p = e.props || L, m = t.props || L, h;
		if (n && Su(n, !1), (h = m.onVnodeBeforeUpdate) && hd(h, n, t, e), f && Wo(t, e, n, "beforeUpdate"), n && Su(n, !0), d && (!e.dynamicChildren || e.dynamicChildren.length !== d.length) && (l = 0, s = !1, d = null), (p.innerHTML && m.innerHTML == null || p.textContent && m.textContent == null) && u(c, ""), d ? E(e.dynamicChildren, d, c, n, r, xu(t, i), o) : s || N(e, t, c, null, n, r, xu(t, i), o, !1), l > 0) {
			if (l & 16) D(c, p, m, n, i);
			else if (l & 2 && p.class !== m.class && a(c, "class", null, m.class, i), l & 4 && a(c, "style", p.style, m.style, i), l & 8) {
				let e = t.dynamicProps;
				for (let t = 0; t < e.length; t++) {
					let r = e[t], o = p[r], s = m[r];
					(s !== o || r === "value") && a(c, r, o, s, i, n);
				}
			}
			l & 1 && e.children !== t.children && u(c, t.children);
		} else !s && d == null && D(c, p, m, n, i);
		((h = m.onVnodeUpdated) || f) && X(() => {
			h && hd(h, n, t, e), f && Wo(t, e, n, "updated");
		}, r);
	}, E = (e, t, n, r, i, a, o) => {
		for (let s = 0; s < t.length; s++) {
			let c = e[s], l = t[s], u = c.el && (c.type === Z || !ed(c, l) || c.shapeFlag & 198) ? d(c.el) : n;
			h(c, l, u, null, r, i, a, o, !0);
		}
	}, D = (e, t, n, r, i) => {
		if (t !== n) {
			if (t !== L) for (let o in t) !$n(o) && !(o in n) && a(e, o, t[o], null, i, r);
			for (let o in n) {
				if ($n(o)) continue;
				let s = n[o], c = t[o];
				s !== c && o !== "value" && a(e, o, c, s, i, r);
			}
			"value" in n && a(e, "value", t.value, n.value, i);
		}
	}, ee = (e, t, n, i, a, o, c, l, u) => {
		let d = t.el = e ? e.el : s(""), f = t.anchor = e ? e.anchor : s(""), { patchFlag: p, dynamicChildren: m, slotScopeIds: h } = t;
		h && (l = l ? l.concat(h) : h), e == null ? (r(d, n, i), r(f, n, i), w(t.children || [], n, f, a, o, c, l, u)) : p > 0 && p & 64 && m && e.dynamicChildren && e.dynamicChildren.length === m.length ? (E(e.dynamicChildren, m, n, a, o, c, l), (t.key != null || a && t === a.subTree) && wu(e, t, !0)) : N(e, t, n, f, a, o, c, l, u);
	}, O = (e, t, n, r, i, a, o, s, c) => {
		t.slotScopeIds = s, e == null ? t.shapeFlag & 512 ? i.ctx.activate(t, n, r, o, c) : k(t, n, r, i, a, o, c) : A(e, t, c);
	}, k = (e, t, n, r, i, a, o) => {
		let s = e.component = vd(e, r, i);
		if (bc(e) && (s.ctx.renderer = F), Dd(s, !1, o), s.asyncDep) {
			if (i && i.registerDep(s, j, o), !e.el) {
				let r = s.subTree = $(Q);
				_(null, r, t, n), e.placeholder = r.el;
			}
		} else j(s, e, t, n, i, a, o);
	}, A = (e, t, n) => {
		let r = t.component = e.component;
		if (Yl(e, t, n)) {
			if (r.asyncDep && !r.asyncResolved) {
				t.el = e.el, M(r, t, n);
				return;
			}
			r.next = t, r.update();
		} else t.el = e.el, r.vnode = t;
	}, j = (e, t, n, r, i, a, o) => {
		let s = () => {
			if (e.isMounted) {
				let { next: t, bu: n, u: r, parent: s, vnode: c } = e;
				{
					let n = Eu(e);
					if (n) {
						t && (t.el = c.el, M(e, t, o)), n.asyncDep.then(() => {
							X(() => {
								e.isUnmounted || l();
							}, i);
						});
						return;
					}
				}
				let u = t, f;
				Su(e, !1), t ? (t.el = c.el, M(e, t, o)) : t = c, n && or(n), (f = t.props && t.props.onVnodeBeforeUpdate) && hd(f, s, t, c), Su(e, !0);
				let p = Gl(e), m = e.subTree;
				e.subTree = p, h(m, p, d(m.el), ce(m), e, i, a), t.el = p.el, u === null && Ql(e, p.el), r && X(r, i), (f = t.props && t.props.onVnodeUpdated) && X(() => hd(f, s, t, c), i), __VUE_PROD_DEVTOOLS__ && jo(e);
			} else {
				let o, { el: s, props: c } = t, { bm: l, m: u, parent: d, root: f, type: p } = e, m = _c(t);
				if (Su(e, !1), l && or(l), !m && (o = c && c.onVnodeBeforeMount) && hd(o, d, t), Su(e, !0), s && fe) {
					let t = () => {
						e.subTree = Gl(e), fe(s, e.subTree, e, i, null);
					};
					m && p.__asyncHydrate ? p.__asyncHydrate(s, e, t) : t();
				} else {
					f.ce && f.ce._hasShadowRoot() && f.ce._injectChildStyle(p, e.parent ? e.parent.type : void 0);
					let o = e.subTree = Gl(e);
					h(null, o, n, r, e, i, a), t.el = o.el;
				}
				if (u && X(u, i), !m && (o = c && c.onVnodeMounted)) {
					let e = t;
					X(() => hd(o, d, e), i);
				}
				(t.shapeFlag & 256 || d && _c(d.vnode) && d.vnode.shapeFlag & 256) && e.a && X(e.a, i), e.isMounted = !0, __VUE_PROD_DEVTOOLS__ && Ao(e), t = n = r = null;
			}
		};
		e.scope.on();
		let c = e.effect = new Kr(s);
		e.scope.off();
		let l = e.update = c.run.bind(c), u = e.job = c.runIfDirty.bind(c);
		u.i = e, u.id = e.uid, c.scheduler = () => go(u), Su(e, !0), l();
	}, M = (e, t, n) => {
		t.component = e;
		let r = e.vnode.props;
		e.vnode = t, e.next = null, ru(e, t.props, r, n), gu(e, t.children, n), li(), yo(e), ui();
	}, N = (e, t, n, r, i, a, o, s, c = !1) => {
		let l = e && e.children, d = e ? e.shapeFlag : 0, f = t.children, { patchFlag: p, shapeFlag: m } = t;
		if (p > 0) {
			if (p & 128) {
				ne(l, f, n, r, i, a, o, s, c);
				return;
			}
			if (p & 256) {
				te(l, f, n, r, i, a, o, s, c);
				return;
			}
		}
		m & 8 ? (d & 16 && se(l, i, a), f !== l && u(n, f)) : d & 16 ? m & 16 ? ne(l, f, n, r, i, a, o, s, c) : se(l, i, a, !0) : (d & 8 && u(n, ""), m & 16 && w(f, n, r, i, a, o, s, c));
	}, te = (e, t, n, r, i, a, o, s, c) => {
		e = e || Fn, t = t || Fn;
		let l = e.length, u = t.length, d = Math.min(l, u), f = 0;
		for (; f < d; f++) {
			let r = t[f] = c ? fd(t[f]) : dd(t[f]);
			h(e[f], r, n, null, i, a, o, s, c);
		}
		l > u ? se(e, i, a, !0, !1, d) : w(t, n, r, i, a, o, s, c, d);
	}, ne = (e, t, n, r, i, a, o, s, c) => {
		let l = 0, u = t.length, d = e.length - 1, f = u - 1;
		for (; l <= d && l <= f;) {
			let r = e[l], u = t[l] = c ? fd(t[l]) : dd(t[l]);
			if (ed(r, u)) h(r, u, n, null, i, a, o, s, c);
			else break;
			l++;
		}
		for (; l <= d && l <= f;) {
			let r = e[d], l = t[f] = c ? fd(t[f]) : dd(t[f]);
			if (ed(r, l)) h(r, l, n, null, i, a, o, s, c);
			else break;
			d--, f--;
		}
		if (l > d) {
			if (l <= f) {
				let e = f + 1, d = e < u ? t[e].el : r;
				for (; l <= f;) h(null, t[l] = c ? fd(t[l]) : dd(t[l]), n, d, i, a, o, s, c), l++;
			}
		} else if (l > f) for (; l <= d;) P(e[l], i, a, !0), l++;
		else {
			let p = l, m = l, g = /* @__PURE__ */ new Map();
			for (l = m; l <= f; l++) {
				let e = t[l] = c ? fd(t[l]) : dd(t[l]);
				e.key != null && g.set(e.key, l);
			}
			let _, v = 0, y = f - m + 1, b = !1, x = 0, S = Array(y);
			for (l = 0; l < y; l++) S[l] = 0;
			for (l = p; l <= d; l++) {
				let r = e[l];
				if (v >= y) {
					P(r, i, a, !0);
					continue;
				}
				let u;
				if (r.key != null) u = g.get(r.key);
				else for (_ = m; _ <= f; _++) if (S[_ - m] === 0 && ed(r, t[_])) {
					u = _;
					break;
				}
				u === void 0 ? P(r, i, a, !0) : (S[u - m] = l + 1, u >= x ? x = u : b = !0, h(r, t[u], n, null, i, a, o, s, c), v++);
			}
			let C = b ? Tu(S) : Fn;
			for (_ = C.length - 1, l = y - 1; l >= 0; l--) {
				let e = m + l, d = t[e], f = t[e + 1], p = e + 1 < u ? f.el || Ou(f) : r;
				S[l] === 0 ? h(null, d, n, p, i, a, o, s, c) : b && (_ < 0 || l !== C[_] ? re(d, n, p, 2) : _--);
			}
		}
	}, re = (e, t, n, a, o = null) => {
		let { el: s, type: c, transition: l, children: u, shapeFlag: d } = e;
		if (d & 6) re(e.component.subTree, t, n, a);
		else if (d & 128) e.suspense.move(t, n, a);
		else if (d & 64) c.move(e, t, n, F);
		else if (c === Z) {
			r(s, t, n);
			for (let e = 0; e < u.length; e++) re(u[e], t, n, a);
			r(e.anchor, t, n);
		} else if (c === Uu) y(e, t, n);
		else if (a !== 2 && d & 1 && l) {
			if (a === 0) l.persisted && !s[_s] ? r(s, t, n) : (l.beforeEnter(s), r(s, t, n), X(() => l.enter(s), o));
			else {
				let { leave: a, delayLeave: o, afterLeave: c } = l, u = () => {
					e.ctx.isUnmounted ? i(s) : r(s, t, n);
				}, d = () => {
					let e = s._isLeaving || !!s[_s];
					s._isLeaving && s[_s](!0), l.persisted && !e ? u() : a(s, () => {
						u(), c && c();
					});
				};
				o ? o(s, u, d) : d();
			}
		} else r(s, t, n);
	}, P = (e, t, n, r = !1, i = !1) => {
		let { type: a, props: o, ref: s, children: c, dynamicChildren: l, shapeFlag: u, patchFlag: d, dirs: f, cacheIndex: p, memo: m } = e;
		if ((d === -2 || l && l.hasOnce) && (i = !1), s != null && (li(), Rs(s, null, n, e, !0), ui()), p != null && (!e.ctx || e.ctx === t) && (t.renderCache[p] = void 0), u & 256) {
			t.ctx.deactivate(e);
			return;
		}
		let h = u & 1 && f, g = !_c(e), _;
		if (g && (_ = o && o.onVnodeBeforeUnmount) && hd(_, t, e), u & 6) oe(e.component, n, r);
		else {
			if (u & 128) {
				e.suspense.unmount(n, r);
				return;
			}
			h && Wo(e, null, t, "beforeUnmount"), u & 64 ? e.type.remove(e, t, n, F, r) : l && !l.hasOnce && (a !== Z || d > 0 && d & 64) ? se(l, t, n, !1, !0) : (a === Z && d & 384 || !i && u & 16) && se(c, t, n), r && ie(e);
		}
		let v = m != null && p == null;
		(g && (_ = o && o.onVnodeUnmounted) || h || v) && X(() => {
			_ && hd(_, t, e), h && Wo(e, null, t, "unmounted"), v && (e.el = null);
		}, n);
	}, ie = (e) => {
		let { type: t, el: n, anchor: r, transition: a } = e;
		if (t === Z) {
			ae(n, r);
			return;
		}
		if (t === Uu) {
			b(e), a && !a.persisted && a.afterLeave && a.afterLeave();
			return;
		}
		let o = () => {
			i(n), a && !a.persisted && a.afterLeave && a.afterLeave();
		};
		if (e.shapeFlag & 1 && a && !a.persisted) {
			let { leave: t, delayLeave: r } = a, i = () => t(n, o);
			r ? r(e.el, o, i) : i();
		} else o();
	}, ae = (e, t) => {
		let n;
		for (; e !== t;) n = f(e), i(e), e = n;
		i(t);
	}, oe = (e, t, n) => {
		let { bum: r, scope: i, job: a, subTree: o, um: s, m: c, a: l } = e;
		Du(c), Du(l), r && or(r), i.stop(), a ? (a.flags |= 8, P(o, e, t, n)) : e.vnode.el && o && (o.transition = e.vnode.transition, P(o, e, t, n)), s && X(s, t), X(() => {
			e.isUnmounted = !0;
		}, t), __VUE_PROD_DEVTOOLS__ && No(e);
	}, se = (e, t, n, r = !1, i = !1, a = 0) => {
		for (let o = a; o < e.length; o++) P(e[o], t, n, r, i);
	}, ce = (e) => {
		if (e.shapeFlag & 6) return ce(e.component.subTree);
		if (e.shapeFlag & 128) return e.suspense.next();
		let t = f(e.anchor || e.el), n = t && t[is];
		return n ? f(n) : t;
	}, le = !1, ue = (e, t, n) => {
		let r;
		e == null ? t._vnode && (P(t._vnode, null, null, !0), r = t._vnode.component) : h(t._vnode || null, e, t, null, null, null, n), t._vnode = e, le || (le = !0, yo(r), bo(), le = !1);
	}, F = {
		p: h,
		um: P,
		m: re,
		r: ie,
		mt: k,
		mc: w,
		pc: N,
		pbc: E,
		n: ce,
		o: e
	}, de, fe;
	return t && ([de, fe] = t(F)), {
		render: ue,
		hydrate: de,
		createApp: Ll(ue, de)
	};
}
function xu({ type: e, props: t }, n) {
	return n === "svg" && e === "foreignObject" || n === "mathml" && e === "annotation-xml" && t && t.encoding && t.encoding.includes("html") ? void 0 : n;
}
function Su({ effect: e, job: t }, n) {
	n ? (e.flags |= 32, t.flags |= 4) : (e.flags &= -33, t.flags &= -5);
}
function Cu(e, t) {
	return (!e || e && !e.pendingBranch) && t && !t.persisted;
}
function wu(e, t, n = !1) {
	let r = e.children, i = t.children;
	if (B(r) && B(i)) for (let e = 0; e < r.length; e++) {
		let t = r[e], a = i[e];
		a.shapeFlag & 1 && !a.dynamicChildren && ((a.patchFlag <= 0 || a.patchFlag === 32) && (a = i[e] = fd(i[e]), a.el = t.el), !n && a.patchFlag !== -2 && wu(t, a)), a.type === Hu && (a.patchFlag === -1 && (a = i[e] = fd(a)), a.el = t.el), a.type === Q && !a.el && (a.el = t.el);
	}
}
function Tu(e) {
	let t = e.slice(), n = [0], r, i, a, o, s, c = e.length;
	for (r = 0; r < c; r++) {
		let c = e[r];
		if (c !== 0) {
			if (i = n[n.length - 1], e[i] < c) {
				t[r] = i, n.push(r);
				continue;
			}
			for (a = 0, o = n.length - 1; a < o;) s = a + o >> 1, e[n[s]] < c ? a = s + 1 : o = s;
			c < e[n[a]] && (a > 0 && (t[r] = n[a - 1]), n[a] = r);
		}
	}
	for (a = n.length, o = n[a - 1]; a-- > 0;) n[a] = o, o = t[o];
	return n;
}
function Eu(e) {
	let t = e.subTree.component;
	if (t) return t.asyncDep && !t.asyncResolved ? t : Eu(t);
}
function Du(e) {
	if (e) for (let t = 0; t < e.length; t++) e[t].flags |= 8;
}
function Ou(e) {
	if (e.placeholder) return e.placeholder;
	let t = e.component;
	return t ? Ou(t.subTree) : null;
}
var ku = (e) => e.__isSuspense, Au = 0, ju = {
	name: "Suspense",
	__isSuspense: !0,
	process(e, t, n, r, i, a, o, s, c, l) {
		if (e == null) Nu(t, n, r, i, a, o, s, c, l);
		else {
			if (a && a.deps > 0 && !e.suspense.isInFallback && !a.isHydrating) {
				t.suspense = e.suspense, t.suspense.vnode = t, t.el = e.el;
				return;
			}
			Pu(e, t, n, r, i, o, s, c, l);
		}
	},
	hydrate: Iu,
	normalize: Lu
};
function Mu(e, t) {
	let n = e.props && e.props[t];
	V(n) && n();
}
function Nu(e, t, n, r, i, a, o, s, c) {
	let { p: l, o: { createElement: u } } = c, d = u("div"), f = e.suspense = Fu(e, i, r, t, d, n, a, o, s, c);
	l(null, f.pendingBranch = e.ssContent, d, null, r, f, a, o), f.deps > 0 ? (Mu(e, "onPending"), Mu(e, "onFallback"), l(null, e.ssFallback, t, n, r, null, a, o), Bu(f, e.ssFallback)) : f.resolve(!1, !0);
}
function Pu(e, t, n, r, i, a, o, s, { p: c, um: l, o: { createElement: u } }) {
	let d = t.suspense = e.suspense;
	d.vnode = t, t.el = e.el;
	let f = t.ssContent, p = t.ssFallback, { activeBranch: m, pendingBranch: h, isInFallback: g, isHydrating: _ } = d;
	if (h) d.pendingBranch = f, ed(h, f) ? (d.deps++, c(h, f, _ ? n : d.hiddenContainer, null, i, d, a, o, s), d.deps--, d.deps <= 0 ? d.resolve() : g && !_ && !d.isFallbackMountPending && (c(m, p, n, r, i, null, a, o, s), Bu(d, p))) : (d.pendingId = Au++, _ ? (d.isHydrating = !1, d.activeBranch = h) : l(h, i, d), d.deps = 0, d.effects.length = 0, d.hiddenContainer = u("div"), g ? (c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0 ? d.resolve() : d.isFallbackMountPending || (c(m, p, n, r, i, null, a, o, s), Bu(d, p))) : m && ed(m, f) ? (c(m, f, n, r, i, d, a, o, s), d.resolve(!0)) : (c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0 && d.resolve()));
	else if (m && ed(m, f)) c(m, f, n, r, i, d, a, o, s), Bu(d, f);
	else if (Mu(t, "onPending"), d.pendingBranch = f, d.pendingId = f.shapeFlag & 512 ? f.component.suspenseId : Au++, c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0) d.resolve();
	else {
		let { timeout: e, pendingId: t } = d;
		e > 0 ? setTimeout(() => {
			d.pendingId === t && d.fallback(p);
		}, e) : e === 0 && d.fallback(p);
	}
}
function Fu(e, t, n, r, i, a, o, s, c, l, u = !1) {
	let { p: d, m: f, um: p, n: m, o: { parentNode: h, remove: g } } = l, _, v = Vu(e);
	v && t && t.pendingBranch && (_ = t.pendingId, t.deps++);
	let y = e.props ? lr(e.props.timeout) : void 0, b = a, x = {
		vnode: e,
		parent: t,
		parentComponent: n,
		namespace: o,
		container: r,
		hiddenContainer: i,
		deps: 0,
		pendingId: Au++,
		timeout: typeof y == "number" ? y : -1,
		activeBranch: null,
		isFallbackMountPending: !1,
		pendingBranch: null,
		isInFallback: !u,
		isHydrating: u,
		isUnmounted: !1,
		effects: [],
		resolve(e = !1, n = !1) {
			let { vnode: r, activeBranch: i, pendingBranch: o, pendingId: s, effects: c, parentComponent: l, container: u, isInFallback: d } = x, g = !1;
			if (x.isHydrating) x.isHydrating = !1;
			else if (!e) {
				g = i && o.transition && o.transition.mode === "out-in";
				let e = !1;
				g && (i.transition.afterLeave = () => {
					s === x.pendingId && (f(o, u, a === b && !e ? m(i) : a, 0), vo(c), d && r.ssFallback && (r.ssFallback.el = null));
				}), i && !x.isFallbackMountPending && (h(i.el) === u && (a = m(i), e = !0), p(i, l, x, !0), !g && d && r.ssFallback && X(() => r.ssFallback.el = null, x)), g || f(o, u, a, 0);
			}
			x.isFallbackMountPending = !1, Bu(x, o), x.pendingBranch = null, x.isInFallback = !1;
			let y = x.parent, S = !1;
			for (; y;) {
				if (y.pendingBranch) {
					for (let e = 0; e < c.length; e++) y.effects.push(c[e]);
					S = !0;
					break;
				}
				y = y.parent;
			}
			!S && !g && vo(c), x.effects = [], v && t && t.pendingBranch && _ === t.pendingId && (_ = void 0, t.deps--, t.deps === 0 && !n && t.resolve()), Mu(r, "onResolve");
		},
		fallback(e) {
			if (!x.pendingBranch) return;
			let { vnode: t, activeBranch: n, parentComponent: r, container: i, namespace: a } = x;
			Mu(t, "onFallback");
			let o = m(n), l = () => {
				if (x.isFallbackMountPending = !1, !x.isInFallback) return;
				let e = x.vnode.ssFallback;
				d(null, e, i, o, r, null, a, s, c), Bu(x, e);
			}, u = e.transition && e.transition.mode === "out-in";
			u && (x.isFallbackMountPending = !0, n.transition.afterLeave = l), x.isInFallback = !0, p(n, r, null, !0), u || l();
		},
		move(e, t, n) {
			x.activeBranch && f(x.activeBranch, e, t, n), x.container = e;
		},
		next() {
			return x.activeBranch && m(x.activeBranch);
		},
		registerDep(e, t, n) {
			let r = !!x.pendingBranch;
			r && x.deps++;
			let i = e.vnode.el;
			e.asyncDep.catch((t) => {
				io(t, e, 0);
			}).then((a) => {
				if (e.isUnmounted || x.isUnmounted || x.pendingId !== e.suspenseId) return;
				if (wd(), i && !e.scope.active) {
					r && --x.deps === 0 && x.resolve();
					return;
				}
				e.asyncResolved = !0;
				let { vnode: s } = e;
				kd(e, a, !1), i && (s.el = i);
				let c = !i && e.subTree.el;
				t(e, s, h(i || e.subTree.el), i ? null : m(e.subTree), x, o, n), c && (s.placeholder = null, g(c)), Ql(e, s.el), r && --x.deps === 0 && x.resolve();
			});
		},
		unmount(e, t) {
			x.isUnmounted = !0, x.activeBranch && p(x.activeBranch, n, e, t), x.pendingBranch && p(x.pendingBranch, n, e, t);
		}
	};
	return x;
}
function Iu(e, t, n, r, i, a, o, s, c) {
	let l = t.suspense = Fu(t, r, n, e.parentNode, document.createElement("div"), null, i, a, o, s, !0), u = c(e, l.pendingBranch = t.ssContent, n, l, a, o);
	return l.deps === 0 && l.resolve(!1, !0), u;
}
function Lu(e) {
	let { shapeFlag: t, children: n } = e, r = t & 32;
	e.ssContent = Ru(r ? n.default : n), e.ssFallback = r ? Ru(n.fallback) : $(Q);
}
function Ru(e) {
	let t;
	if (V(e)) {
		let n = Ju && e._c;
		n && (e._d = !1, Ku()), e = e(), n && (e._d = !0, t = Gu, qu());
	}
	return B(e) && (e = Kl(e)), e = dd(e), t && !e.dynamicChildren && (e.dynamicChildren = t.filter((t) => t !== e)), e;
}
function zu(e, t) {
	t && t.pendingBranch ? B(e) ? t.effects.push(...e) : t.effects.push(e) : vo(e);
}
function Bu(e, t) {
	e.activeBranch = t;
	let { vnode: n, parentComponent: r } = e, i = t.el;
	for (; !i && t.component;) t = t.component.subTree, i = t.el;
	n.el = i, r && r.subTree === n && (r.vnode.el = i, Ql(r, i));
}
function Vu(e) {
	let t = e.props && e.props.suspensible;
	return t != null && t !== !1;
}
var Z = /* @__PURE__ */ Symbol.for("v-fgt"), Hu = /* @__PURE__ */ Symbol.for("v-txt"), Q = /* @__PURE__ */ Symbol.for("v-cmt"), Uu = /* @__PURE__ */ Symbol.for("v-stc"), Wu = [], Gu = null;
function Ku(e = !1) {
	Wu.push(Gu = e ? null : []);
}
function qu() {
	Wu.pop(), Gu = Wu[Wu.length - 1] || null;
}
var Ju = 1;
function Yu(e, t = !1) {
	Ju += e, e < 0 && Gu && t && (Gu.hasOnce = !0);
}
function Xu(e) {
	return e.dynamicChildren = Ju > 0 ? Gu || Fn : null, qu(), Ju > 0 && Gu && Gu.push(e), e;
}
function Zu(e, t, n, r, i, a) {
	return Xu(id(e, t, n, r, i, a, !0));
}
function Qu(e, t, n, r, i) {
	return Xu($(e, t, n, r, i, !0));
}
function $u(e) {
	return e ? e.__v_isVNode === !0 : !1;
}
function ed(e, t) {
	return e.type === t.type && e.key === t.key;
}
function td(e) {}
var nd = ({ key: e }) => e ?? null, rd = ({ ref: e, ref_key: t, ref_for: n }) => (typeof e == "number" && (e = "" + e), e == null ? null : H(e) || /* @__PURE__ */ Y(e) || V(e) ? {
	i: Io,
	r: e,
	k: t,
	f: !!n
} : e);
function id(e, t = null, n = null, r = 0, i = null, a = e === Z ? 0 : 1, o = !1, s = !1) {
	let c = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e,
		props: t,
		key: t && nd(t),
		ref: t && rd(t),
		scopeId: Lo,
		slotScopeIds: null,
		children: n,
		component: null,
		suspense: null,
		ssContent: null,
		ssFallback: null,
		dirs: null,
		transition: null,
		el: null,
		anchor: null,
		target: null,
		targetStart: null,
		targetAnchor: null,
		staticCount: 0,
		shapeFlag: a,
		patchFlag: r,
		dynamicProps: i,
		dynamicChildren: null,
		appContext: null,
		ctx: Io
	};
	return s ? (pd(c, n), a & 128 && e.normalize(c)) : n && (c.shapeFlag |= H(n) ? 8 : 16), Ju > 0 && !o && Gu && (c.patchFlag > 0 || a & 6) && c.patchFlag !== 32 && Gu.push(c), c;
}
var $ = ad;
function ad(e, t = null, n = null, r = 0, i = null, a = !1) {
	if ((!e || e === Wc) && (e = Q), $u(e)) {
		let r = sd(e, t, !0);
		return n && pd(r, n), Ju > 0 && !a && Gu && (r.shapeFlag & 6 ? Gu[Gu.indexOf(e)] = r : Gu.push(r)), r.patchFlag = -2, r;
	}
	if (Hd(e) && (e = e.__vccOpts), t) {
		t = od(t);
		let { class: e, style: n } = t;
		e && !H(e) && (t.class = yr(e)), U(n) && (/* @__PURE__ */ fa(n) && !B(n) && (n = R({}, n)), t.style = pr(n));
	}
	let o = H(e) ? 1 : ku(e) ? 128 : as(e) ? 64 : U(e) ? 4 : V(e) ? 2 : 0;
	return id(e, t, n, r, i, o, a, !0);
}
function od(e) {
	return e ? /* @__PURE__ */ fa(e) || tu(e) ? R({}, e) : e : null;
}
function sd(e, t, n = !1, r = !1) {
	let { props: i, ref: a, patchFlag: o, children: s, transition: c } = e, l = t ? md(i || {}, t) : i, u = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e.type,
		props: l,
		key: l && nd(l),
		ref: t && t.ref ? n && a ? B(a) ? a.concat(rd(t)) : [a, rd(t)] : rd(t) : a,
		scopeId: e.scopeId,
		slotScopeIds: e.slotScopeIds,
		children: s,
		target: e.target,
		targetStart: e.targetStart,
		targetAnchor: e.targetAnchor,
		staticCount: e.staticCount,
		shapeFlag: e.shapeFlag,
		patchFlag: t && e.type !== Z ? o === -1 ? 16 : o | 16 : o,
		dynamicProps: e.dynamicProps,
		dynamicChildren: e.dynamicChildren,
		appContext: e.appContext,
		dirs: e.dirs,
		transition: c,
		component: e.component,
		suspense: e.suspense,
		ssContent: e.ssContent && sd(e.ssContent),
		ssFallback: e.ssFallback && sd(e.ssFallback),
		placeholder: e.placeholder,
		el: e.el,
		anchor: e.anchor,
		ctx: e.ctx,
		ce: e.ce,
		cacheIndex: e.cacheIndex
	};
	return c && r && As(u, c.clone(u)), u;
}
function cd(e = " ", t = 0) {
	return $(Hu, null, e, t);
}
function ld(e, t) {
	let n = $(Uu, null, e);
	return n.staticCount = t, n;
}
function ud(e = "", t = !1) {
	return t ? (Ku(), Qu(Q, null, e)) : $(Q, null, e);
}
function dd(e) {
	return e == null || typeof e == "boolean" ? $(Q) : B(e) ? $(Z, null, e.slice()) : $u(e) ? fd(e) : $(Hu, null, String(e));
}
function fd(e) {
	return e.el === null && e.patchFlag !== -1 || e.memo ? e : sd(e);
}
function pd(e, t) {
	let n = 0, { shapeFlag: r } = e;
	if (t == null) t = null;
	else if (B(t)) n = 16;
	else if (typeof t == "object") {
		if (r & 65) {
			let n = t.default;
			n && (n._c && (n._d = !1), pd(e, n()), n._c && (n._d = !0));
			return;
		}
		{
			n = 32;
			let r = t._;
			!r && !tu(t) ? t._ctx = Io : r === 3 && Io && (Io.slots._ === 1 ? t._ = 1 : (t._ = 2, e.patchFlag |= 1024));
		}
	} else if (V(t)) {
		if (r & 65) {
			pd(e, { default: t });
			return;
		}
		t = {
			default: t,
			_ctx: Io
		}, n = 32;
	} else t = String(t), r & 64 ? (n = 16, t = [cd(t)]) : n = 8;
	e.children = t, e.shapeFlag |= n;
}
function md(...e) {
	let t = {};
	for (let n = 0; n < e.length; n++) {
		let r = e[n];
		for (let e in r) if (e === "class") t.class !== r.class && (t.class = yr([t.class, r.class]));
		else if (e === "style") t.style = pr([t.style, r.style]);
		else if (Rn(e)) {
			let n = t[e], i = r[e];
			i && n !== i && !(B(n) && n.includes(i)) ? t[e] = n ? [].concat(n, i) : i : i == null && n == null && !zn(e) && (t[e] = i);
		} else e !== "" && (t[e] = r[e]);
	}
	return t;
}
function hd(e, t, n, r = null) {
	ro(e, t, 7, [n, r]);
}
var gd = Fl(), _d = 0;
function vd(e, t, n) {
	let r = e.type, i = (t ? t.appContext : e.appContext) || gd, a = {
		uid: _d++,
		vnode: e,
		type: r,
		parent: t,
		appContext: i,
		root: null,
		next: null,
		subTree: null,
		effect: null,
		update: null,
		job: null,
		scope: new Vr(!0),
		render: null,
		proxy: null,
		exposed: null,
		exposeProxy: null,
		withProxy: null,
		provides: t ? t.provides : Object.create(i.provides),
		ids: t ? t.ids : [
			"",
			0,
			0
		],
		accessCache: null,
		renderCache: [],
		components: null,
		directives: null,
		propsOptions: su(r, i),
		emitsOptions: Ul(r, i),
		emit: null,
		emitted: null,
		propsDefaults: L,
		inheritAttrs: r.inheritAttrs,
		ctx: L,
		data: L,
		props: L,
		attrs: L,
		slots: L,
		refs: L,
		setupState: L,
		setupContext: null,
		suspense: n,
		suspenseId: n ? n.pendingId : 0,
		asyncDep: null,
		asyncResolved: !1,
		isMounted: !1,
		isUnmounted: !1,
		isDeactivated: !1,
		bc: null,
		c: null,
		bm: null,
		m: null,
		bu: null,
		u: null,
		um: null,
		bum: null,
		da: null,
		a: null,
		rtg: null,
		rtc: null,
		ec: null,
		sp: null
	};
	return a.ctx = { _: a }, a.root = t ? t.root : a, a.emit = Vl.bind(null, a), e.ce && e.ce(a), a;
}
var yd = null, bd = () => yd || Io, xd, Sd;
{
	let e = dr(), t = (t, n) => {
		let r;
		return (r = e[t]) || (r = e[t] = []), r.push(n), (e) => {
			r.length > 1 ? r.forEach((t) => t(e)) : r[0](e);
		};
	};
	xd = t("__VUE_INSTANCE_SETTERS__", (e) => yd = e), Sd = t("__VUE_SSR_SETTERS__", (e) => Ed = e);
}
var Cd = (e) => {
	let t = yd;
	return xd(e), e.scope.on(), () => {
		e.scope.off(), xd(t);
	};
}, wd = () => {
	yd && yd.scope.off(), xd(null);
};
function Td(e) {
	return e.vnode.shapeFlag & 4;
}
var Ed = !1;
function Dd(e, t = !1, n = !1) {
	t && Sd(t);
	let { props: r, children: i } = e.vnode, a = Td(e);
	nu(e, r, a, t), hu(e, i, n || t);
	let o = a ? Od(e, t) : void 0;
	return t && Sd(!1), o;
}
function Od(e, t) {
	let n = e.type;
	e.accessCache = /* @__PURE__ */ Object.create(null), e.proxy = new Proxy(e.ctx, rl);
	let { setup: r } = n;
	if (r) {
		li();
		let n = e.setupContext = r.length > 1 ? Id(e) : null, i = Cd(e), a = no(r, e, 0, [e.props, n]), o = qn(a);
		if (ui(), i(), (o || e.sp) && !_c(e) && Ps(e), o) {
			if (a.then(wd, wd), t) return a.then((n) => {
				Sd(!0);
				try {
					kd(e, n, t);
				} finally {
					Sd(!1);
				}
			}).catch((t) => {
				io(t, e, 0);
			});
			e.asyncDep = a;
		} else kd(e, a, t);
	} else Pd(e, t);
}
function kd(e, t, n) {
	V(t) ? e.type.__ssrInlineRender ? e.ssrRender = t : e.render = t : U(t) && (__VUE_PROD_DEVTOOLS__ && (e.devtoolsRawSetupState = t), e.setupState = wa(t)), Pd(e, n);
}
var Ad, jd;
function Md(e) {
	Ad = e, jd = (e) => {
		e.render._rc && (e.withProxy = new Proxy(e.ctx, il));
	};
}
var Nd = () => !Ad;
function Pd(e, t, n) {
	let r = e.type;
	if (!e.render) {
		if (!t && Ad && !r.render) {
			let t = r.template || __VUE_OPTIONS_API__ && Tl(e).template;
			if (t) {
				let { isCustomElement: n, compilerOptions: i } = e.appContext.config, { delimiters: a, compilerOptions: o } = r, s = R(R({
					isCustomElement: n,
					delimiters: a
				}, i), o);
				r.render = Ad(t, s);
			}
		}
		e.render = r.render || In, jd && jd(e);
	}
	if (__VUE_OPTIONS_API__) {
		let t = Cd(e);
		li();
		try {
			xl(e);
		} finally {
			ui(), t();
		}
	}
}
var Fd = { get(e, t) {
	return bi(e, "get", ""), e[t];
} };
function Id(e) {
	return {
		attrs: new Proxy(e.attrs, Fd),
		slots: e.slots,
		emit: e.emit,
		expose: (t) => {
			e.exposed = t || {};
		}
	};
}
function Ld(e) {
	return e.exposed ? e.exposeProxy || (e.exposeProxy = new Proxy(wa(pa(e.exposed)), {
		get(t, n) {
			if (n in t) return t[n];
			if (n in tl) return tl[n](e);
		},
		has(e, t) {
			return t in e || t in tl;
		}
	})) : e.proxy;
}
var Rd = /(?:^|[-_])\w/g, zd = (e) => e.replace(Rd, (e) => e.toUpperCase()).replace(/[-_]/g, "");
function Bd(e, t = !0) {
	return V(e) ? e.displayName || e.name : e.name || t && e.__name;
}
function Vd(e, t, n = !1) {
	let r = Bd(t);
	if (!r && t.__file) {
		let e = t.__file.match(/([^/\\]+)\.\w+$/);
		e && (r = e[1]);
	}
	if (!r && e) {
		let n = (e) => {
			for (let n in e) if (e[n] === t) return n;
		};
		r = n(e.components) || e.parent && n(e.parent.type.components) || n(e.appContext.components);
	}
	return r ? zd(r) : n ? "App" : "Anonymous";
}
function Hd(e) {
	return V(e) && "__vccOpts" in e;
}
var Ud = (e, t) => /* @__PURE__ */ Na(e, t, Ed);
function Wd(e, t, n) {
	try {
		Yu(-1);
		let r = arguments.length;
		return r === 2 ? U(t) && !B(t) ? $u(t) ? $(e, null, [t]) : $(e, t) : $(e, null, t) : (r > 3 ? n = Array.prototype.slice.call(arguments, 2) : r === 3 && $u(n) && (n = [n]), $(e, t, n));
	} finally {
		Yu(1);
	}
}
function Gd() {}
function Kd(e, t, n, r) {
	let i = n[r];
	if (i && qd(i, e)) return i;
	let a = t();
	return a.memo = e.slice(), a.cacheIndex = r, n[r] = a;
}
function qd(e, t) {
	let n = e.memo;
	if (n.length != t.length) return !1;
	for (let e = 0; e < n.length; e++) if (G(n[e], t[e])) return !1;
	return Ju > 0 && Gu && Gu.push(e), !0;
}
var Jd = "3.5.43", Yd = In, Xd = to, Zd = Co, Qd = Do, $d = {
	createComponentInstance: vd,
	setupComponent: Dd,
	renderComponentRoot: Gl,
	setCurrentRenderingInstance: Ro,
	isVNode: $u,
	normalizeVNode: dd,
	getComponentPublicInstance: Ld,
	ensureValidVNode: Qc,
	pushWarningContext: Wa,
	popWarningContext: Ga
}, ef = void 0, tf = typeof window < "u" && window.trustedTypes;
if (tf) try {
	ef = /* @__PURE__ */ tf.createPolicy("vue", { createHTML: (e) => e });
} catch {}
var nf = ef ? (e) => ef.createHTML(e) : (e) => e, rf = "http://www.w3.org/2000/svg", af = "http://www.w3.org/1998/Math/MathML", of = typeof document < "u" ? document : null, sf = of && /* @__PURE__ */ of.createElement("template"), cf = {
	insert: (e, t, n) => {
		t.insertBefore(e, n || null);
	},
	remove: (e) => {
		let t = e.parentNode;
		t && t.removeChild(e);
	},
	createElement: (e, t, n, r) => {
		let i = t === "svg" ? of.createElementNS(rf, e) : t === "mathml" ? of.createElementNS(af, e) : n ? of.createElement(e, { is: n }) : of.createElement(e);
		return e === "select" && r && r.multiple != null && i.setAttribute("multiple", r.multiple), i;
	},
	createText: (e) => of.createTextNode(e),
	createComment: (e) => of.createComment(e),
	setText: (e, t) => {
		e.nodeValue = t;
	},
	setElementText: (e, t) => {
		e.textContent = t;
	},
	parentNode: (e) => e.parentNode,
	nextSibling: (e) => e.nextSibling,
	querySelector: (e) => of.querySelector(e),
	setScopeId(e, t) {
		e.setAttribute(t, "");
	},
	insertStaticContent(e, t, n, r, i, a) {
		let o = n ? n.previousSibling : t.lastChild;
		if (i && (i === a || i.nextSibling)) for (; t.insertBefore(i.cloneNode(!0), n), i !== a && (i = i.nextSibling););
		else {
			sf.innerHTML = nf(r === "svg" ? `<svg>${e}</svg>` : r === "mathml" ? `<math>${e}</math>` : e);
			let i = sf.content;
			if (r === "svg" || r === "mathml") {
				let e = i.firstChild;
				for (; e.firstChild;) i.appendChild(e.firstChild);
				i.removeChild(e);
			}
			t.insertBefore(i, n);
		}
		return [o ? o.nextSibling : t.firstChild, n ? n.previousSibling : t.lastChild];
	}
}, lf = "transition", uf = "animation", df = /* @__PURE__ */ Symbol("_vtc"), ff = {
	name: String,
	type: String,
	css: {
		type: Boolean,
		default: !0
	},
	duration: [
		String,
		Number,
		Object
	],
	enterFromClass: String,
	enterActiveClass: String,
	enterToClass: String,
	appearFromClass: String,
	appearActiveClass: String,
	appearToClass: String,
	leaveFromClass: String,
	leaveActiveClass: String,
	leaveToClass: String
}, pf = /* @__PURE__ */ R({}, xs, ff), mf = /* @__PURE__ */ ((e) => (e.displayName = "Transition", e.props = pf, e))((e, { slots: t }) => Wd(Ts, _f(e), t)), hf = (e, t = []) => {
	B(e) ? e.forEach((e) => e(...t)) : e && e(...t);
}, gf = (e) => e ? B(e) ? e.some((e) => e.length > 1) : e.length > 1 : !1;
function _f(e) {
	let t = {};
	for (let n in e) n in ff || (t[n] = e[n]);
	if (e.css === !1) return t;
	let { name: n = "v", type: r, duration: i, enterFromClass: a = `${n}-enter-from`, enterActiveClass: o = `${n}-enter-active`, enterToClass: s = `${n}-enter-to`, appearFromClass: c = a, appearActiveClass: l = o, appearToClass: u = s, leaveFromClass: d = `${n}-leave-from`, leaveActiveClass: f = `${n}-leave-active`, leaveToClass: p = `${n}-leave-to` } = e, m = vf(i), h = m && m[0], g = m && m[1], { onBeforeEnter: _, onEnter: v, onEnterCancelled: y, onLeave: b, onLeaveCancelled: x, onBeforeAppear: S = _, onAppear: C = v, onAppearCancelled: w = y } = t, T = (e, t, n, r) => {
		e._enterCancelled = r, xf(e, t ? u : s), xf(e, t ? l : o), n && n();
	}, E = (e, t) => {
		e._isLeaving = !1, xf(e, d), xf(e, p), xf(e, f), t && t();
	}, D = (e) => (t, n) => {
		let i = e ? C : v, o = () => T(t, e, n);
		hf(i, [t, o]), Sf(() => {
			xf(t, e ? c : a), bf(t, e ? u : s), gf(i) || wf(t, r, h, o);
		});
	};
	return R(t, {
		onBeforeEnter(e) {
			hf(_, [e]), bf(e, a), bf(e, o);
		},
		onBeforeAppear(e) {
			hf(S, [e]), bf(e, c), bf(e, l);
		},
		onEnter: D(!1),
		onAppear: D(!0),
		onLeave(e, t) {
			e._isLeaving = !0;
			let n = () => E(e, t);
			bf(e, d), e._enterCancelled ? (bf(e, f), Of(e)) : (Of(e), bf(e, f)), Sf(() => {
				e._isLeaving && (xf(e, d), bf(e, p), gf(b) || wf(e, r, g, n));
			}), hf(b, [e, n]);
		},
		onEnterCancelled(e) {
			T(e, !1, void 0, !0), hf(y, [e]);
		},
		onAppearCancelled(e) {
			T(e, !0, void 0, !0), hf(w, [e]);
		},
		onLeaveCancelled(e) {
			E(e), hf(x, [e]);
		}
	});
}
function vf(e) {
	if (e == null) return null;
	if (U(e)) return [yf(e.enter), yf(e.leave)];
	{
		let t = yf(e);
		return [t, t];
	}
}
function yf(e) {
	return lr(e);
}
function bf(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.add(t)), (e[df] || (e[df] = /* @__PURE__ */ new Set())).add(t);
}
function xf(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.remove(t));
	let n = e[df];
	n && (n.delete(t), n.size || (e[df] = void 0));
}
function Sf(e) {
	requestAnimationFrame(() => {
		requestAnimationFrame(e);
	});
}
var Cf = 0;
function wf(e, t, n, r) {
	let i = e._endId = ++Cf, a = () => {
		i === e._endId && r();
	};
	if (n != null) return setTimeout(a, n);
	let { type: o, timeout: s, propCount: c } = Tf(e, t);
	if (!o) return r();
	let l = o + "end", u = 0, d = () => {
		e.removeEventListener(l, f), a();
	}, f = (t) => {
		t.target === e && ++u >= c && d();
	};
	setTimeout(() => {
		u < c && d();
	}, s + 1), e.addEventListener(l, f);
}
function Tf(e, t) {
	let n = window.getComputedStyle(e), r = (e) => (n[e] || "").split(", "), i = r(`${lf}Delay`), a = r(`${lf}Duration`), o = Ef(i, a), s = r(`${uf}Delay`), c = r(`${uf}Duration`), l = Ef(s, c), u = null, d = 0, f = 0;
	t === lf ? o > 0 && (u = lf, d = o, f = a.length) : t === uf ? l > 0 && (u = uf, d = l, f = c.length) : (d = Math.max(o, l), u = d > 0 ? o > l ? lf : uf : null, f = u ? u === lf ? a.length : c.length : 0);
	let p = u === lf && /\b(?:transform|all)(?:,|$)/.test(r(`${lf}Property`).toString());
	return {
		type: u,
		timeout: d,
		propCount: f,
		hasTransform: p
	};
}
function Ef(e, t) {
	for (; e.length < t.length;) e = e.concat(e);
	return Math.max(...t.map((t, n) => Df(t) + Df(e[n])));
}
function Df(e) {
	return e === "auto" ? 0 : Number(e.slice(0, -1).replace(",", ".")) * 1e3;
}
function Of(e) {
	return (e ? e.ownerDocument : document).body.offsetHeight;
}
function kf(e, t, n) {
	let r = e[df];
	r && (t = (t ? [t, ...r] : [...r]).join(" ")), t == null ? e.removeAttribute("class") : n ? e.setAttribute("class", t) : e.className = t;
}
var Af = /* @__PURE__ */ Symbol("_vod"), jf = /* @__PURE__ */ Symbol("_vsh"), Mf = {
	name: "show",
	beforeMount(e, { value: t }, { transition: n }) {
		e[Af] = e.style.display === "none" ? "" : e.style.display, n && t ? n.beforeEnter(e) : Nf(e, t);
	},
	mounted(e, { value: t }, { transition: n }) {
		n && t && n.enter(e);
	},
	updated(e, { value: t, oldValue: n }, { transition: r }) {
		!t != !n && (r ? t ? (r.beforeEnter(e), Nf(e, !0), r.enter(e)) : r.leave(e, () => {
			Nf(e, !1);
		}) : Nf(e, t));
	},
	beforeUnmount(e, { value: t }) {
		Nf(e, t);
	}
};
function Nf(e, t) {
	e.style.display = t ? e[Af] : "none", e[jf] = !t;
}
function Pf() {
	Mf.getSSRProps = ({ value: e }) => {
		if (!e) return { style: { display: "none" } };
	};
}
var Ff = /* @__PURE__ */ Symbol("");
function If(e) {
	let t = bd();
	if (!t) return;
	let n = t.ut = (n = e(t.proxy)) => {
		Array.from(document.querySelectorAll(`[data-v-owner="${t.uid}"]`)).forEach((e) => Rf(e, n));
	}, r = () => {
		let r = e(t.proxy);
		t.ce ? Rf(t.ce, r) : Lf(t.subTree, r), n(r);
	};
	Nc(() => {
		vo(r);
	}), Mc(() => {
		$o(r, In, { flush: "post" });
		let e = new MutationObserver(r);
		e.observe(t.subTree.el.parentNode, { childList: !0 }), Ic(() => e.disconnect());
	});
}
function Lf(e, t) {
	if (e.shapeFlag & 128) {
		let n = e.suspense;
		e = n.activeBranch, n.pendingBranch && !n.isHydrating && n.effects.push(() => {
			Lf(n.activeBranch, t);
		});
	}
	for (; e.component;) e = e.component.subTree;
	if (e.shapeFlag & 1 && e.el) Rf(e.el, t);
	else if (e.type === Z) e.children.forEach((e) => Lf(e, t));
	else if (e.type === Uu) {
		let { el: n, anchor: r } = e;
		for (; n && (Rf(n, t), n !== r);) n = n.nextSibling;
	}
}
function Rf(e, t) {
	if (e.nodeType === 1) {
		let n = e.style, r = "";
		for (let e in t) {
			let i = Br(t[e]);
			n.setProperty(`--${e}`, i), r += `--${e}: ${i};`;
		}
		n[Ff] = r;
	}
}
var zf = /(?:^|;)\s*display\s*:/;
function Bf(e, t, n) {
	let r = e.style, i = H(n), a = !1;
	if (n && !i) {
		if (t) {
			if (H(t)) for (let e of t.split(";")) {
				let t = e.slice(0, e.indexOf(":")).trim();
				n[t] ?? Hf(r, t, "");
			}
			else for (let e in t) n[e] ?? Hf(r, e, "");
		}
		for (let i in n) {
			i === "display" && (a = !0);
			let o = n[i];
			o == null ? Hf(r, i, "") : Kf(e, i, !H(t) && t ? t[i] : void 0, o) || Hf(r, i, o);
		}
	} else if (i) {
		if (t !== n) {
			let e = r[Ff];
			e && (n += ";" + e), r.cssText = n, a = zf.test(n);
		}
	} else t && e.removeAttribute("style");
	Af in e && (e[Af] = a ? r.display : "", e[jf] && (r.display = "none"));
}
var Vf = /\s*!important$/;
function Hf(e, t, n) {
	if (B(n)) n.forEach((n) => Hf(e, t, n));
	else if (n ?? (n = ""), t.startsWith("--")) Vf.test(n) ? e.setProperty(t, n.replace(Vf, ""), "important") : e.setProperty(t, n);
	else {
		let r = Gf(e, t);
		Vf.test(n) ? e.setProperty(rr(r), n.replace(Vf, ""), "important") : e[r] = n;
	}
}
var Uf = [
	"Webkit",
	"Moz",
	"ms"
], Wf = {};
function Gf(e, t) {
	let n = Wf[t];
	if (n) return n;
	let r = W(t);
	if (r !== "filter" && r in e) return Wf[t] = r;
	r = ir(r);
	for (let n = 0; n < Uf.length; n++) {
		let i = Uf[n] + r;
		if (i in e) return Wf[t] = i;
	}
	return t;
}
function Kf(e, t, n, r) {
	return e.tagName === "TEXTAREA" && (t === "width" || t === "height") && H(r) && n === r;
}
var qf = "http://www.w3.org/1999/xlink";
function Jf(e, t, n, r, i, a = Sr(t)) {
	r && t.startsWith("xlink:") ? n == null ? e.removeAttributeNS(qf, t.slice(6, t.length)) : e.setAttributeNS(qf, t, n) : n == null || a && !wr(n) ? e.removeAttribute(t) : e.setAttribute(t, a ? "" : Kn(n) ? String(n) : n);
}
function Yf(e, t, n, r, i) {
	if (t === "innerHTML" || t === "textContent") {
		n != null && (e[t] = t === "innerHTML" ? nf(n) : n);
		return;
	}
	let a = e.tagName;
	if (t === "value" && a !== "PROGRESS" && !a.includes("-")) {
		let r = a === "OPTION" ? e.getAttribute("value") || "" : e.value, i = n == null ? e.type === "checkbox" ? "on" : "" : String(n);
		(r !== i || !("_value" in e)) && (e.value = i), n ?? e.removeAttribute(t), e._value = n;
		return;
	}
	let o = !1;
	if (n === "" || n == null) {
		let r = typeof e[t];
		r === "boolean" ? n = wr(n) : n == null && r === "string" ? (n = "", o = !0) : r === "number" && (n = 0, o = !0);
	}
	try {
		e[t] = n;
	} catch {}
	o && e.removeAttribute(i || t);
}
function Xf(e, t, n, r) {
	e.addEventListener(t, n, r);
}
function Zf(e, t, n, r) {
	e.removeEventListener(t, n, r);
}
var Qf = /* @__PURE__ */ Symbol("_vei");
function $f(e, t, n, r, i = null) {
	let a = e[Qf] || (e[Qf] = {}), o = a[t];
	if (r && o) o.value = r;
	else {
		let [n, s] = np(t);
		r ? Xf(e, n, a[t] = op(r, i), s) : o && (Zf(e, n, o, s), a[t] = void 0);
	}
}
var ep = /(Once|Passive|Capture)$/, tp = /^on:?(?:Once|Passive|Capture)$/;
function np(e) {
	let t, n;
	for (; (n = e.match(ep)) && !tp.test(e);) t || (t = {}), e = e.slice(0, e.length - n[1].length), t[n[1].toLowerCase()] = !0;
	return [e[2] === ":" ? e.slice(3) : rr(e.slice(2)), t];
}
var rp = 0, ip = /* @__PURE__ */ Promise.resolve(), ap = () => rp || (ip.then(() => rp = 0), rp = Date.now());
function op(e, t) {
	let n = (e) => {
		if (!e._vts) e._vts = Date.now();
		else if (e._vts <= n.attached) return;
		let r = n.value;
		if (B(r)) {
			let n = e.stopImmediatePropagation;
			e.stopImmediatePropagation = () => {
				n.call(e), e._stopped = !0;
			};
			let i = r.slice(), a = [e];
			for (let n = 0; n < i.length && !e._stopped; n++) {
				let e = i[n];
				e && ro(e, t, 5, a);
			}
		} else ro(r, t, 5, [e]);
	};
	return n.value = e, n.attached = ap(), n;
}
var sp = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && e.charCodeAt(2) > 96 && e.charCodeAt(2) < 123, cp = (e, t, n, r, i, a) => {
	let o = i === "svg";
	t === "class" ? kf(e, r, o) : t === "style" ? Bf(e, n, r) : Rn(t) ? zn(t) || $f(e, t, n, r, a) : (t[0] === "." ? (t = t.slice(1), 1) : t[0] === "^" ? (t = t.slice(1), 0) : lp(e, t, r, o)) ? (Yf(e, t, r), !e.tagName.includes("-") && (t === "value" || t === "checked" || t === "selected") && Jf(e, t, r, o, a, t !== "value")) : e._isVueCE && (up(e, t) || e._def.__asyncLoader && (/[A-Z]/.test(t) || !H(r))) ? Yf(e, W(t), r, a, t) : (t === "true-value" ? e._trueValue = r : t === "false-value" && (e._falseValue = r), Jf(e, t, r, o));
};
function lp(e, t, n, r) {
	if (r) return !!(t === "innerHTML" || t === "textContent" || t in e && sp(t) && V(n));
	if (t === "spellcheck" || t === "draggable" || t === "translate" || t === "autocorrect" || t === "sandbox" && e.tagName === "IFRAME" || t === "form" || t === "list" && e.tagName === "INPUT" || t === "type" && e.tagName === "TEXTAREA") return !1;
	if (t === "width" || t === "height") {
		let t = e.tagName;
		if (t === "IMG" || t === "VIDEO" || t === "CANVAS" || t === "SOURCE") return !1;
	}
	return sp(t) && H(n) ? !1 : t in e;
}
function up(e, t) {
	let n = e._def.props;
	if (!n) return !1;
	let r = W(t);
	return Array.isArray(n) ? n.some((e) => W(e) === r) : Object.keys(n).some((e) => W(e) === r);
}
var dp = {};
// @__NO_SIDE_EFFECTS__
function fp(e, t, n) {
	let r = /* @__PURE__ */ Ms(e, t);
	Zn(r) && (r = R({}, r, t));
	class i extends hp {
		constructor(e) {
			super(r, e, n);
		}
	}
	return i.def = r, i;
}
var pp = /* @__NO_SIDE_EFFECTS__ */ ((e, t) => /* @__PURE__ */ fp(e, t, sm)), mp = typeof HTMLElement < "u" ? HTMLElement : class {}, hp = class e extends mp {
	constructor(e, t = {}, n = om) {
		super(), this._def = e, this._props = t, this._createApp = n, this._isVueCE = !0, this._instance = null, this._app = null, this._nonce = this._def.nonce, this._connected = !1, this._resolved = !1, this._patching = !1, this._dirty = !1, this._numberProps = null, this._styleChildren = /* @__PURE__ */ new WeakSet(), this._styleAnchors = /* @__PURE__ */ new WeakMap(), this._ob = null, this.shadowRoot && n !== om ? this._root = this.shadowRoot : e.shadowRoot === !1 ? this._root = this : (this.attachShadow(R({}, e.shadowRootOptions, { mode: "open" })), this._root = this.shadowRoot);
	}
	connectedCallback() {
		if (!this.isConnected) return;
		!this.shadowRoot && !this._resolved && this._parseSlots(), this._connected = !0;
		let t = this;
		for (; t = t && (t.assignedSlot || t.parentNode || t.host);) if (t instanceof e) {
			this._parent = t;
			break;
		}
		this._instance || (this._resolved ? this._mount(this._def) : t && t._pendingResolve ? this._pendingResolve = t._pendingResolve.then(() => {
			if (this._pendingResolve = void 0, this.isConnected) return this._resolveDef();
		}) : this._resolveDef());
	}
	_setParent(e = this._parent) {
		e && (this._instance.parent = e._instance, this._inheritParentContext(e));
	}
	_inheritParentContext(e = this._parent) {
		e && this._app && Object.setPrototypeOf(this._app._context.provides, e._instance.provides);
	}
	disconnectedCallback() {
		this._connected = !1, mo(() => {
			this._connected || (this._ob && (this._ob.disconnect(), this._ob = null), this._app && this._app.unmount(), this._instance && (this._instance.ce = void 0), this._app = this._instance = null, this._teleportTargets && (this._teleportTargets.clear(), this._teleportTargets = void 0));
		});
	}
	_processMutations(e) {
		for (let t of e) this._setAttr(t.attributeName);
	}
	_resolveDef() {
		if (this._pendingResolve) return this._pendingResolve;
		for (let e = 0; e < this.attributes.length; e++) this._setAttr(this.attributes[e].name);
		this._ob = new MutationObserver(this._processMutations.bind(this)), this._ob.observe(this, { attributes: !0 });
		let e = (e, t = !1) => {
			this._resolved = !0, this._pendingResolve = void 0;
			let { props: n, styles: r } = e, i;
			if (n && !B(n)) for (let e in n) {
				let t = n[e];
				(t === Number || t && t.type === Number) && (e in this._props && (this._props[e] = lr(this._props[e])), (i || (i = /* @__PURE__ */ Object.create(null)))[W(e)] = !0);
			}
			this._numberProps = i, this._resolveProps(e), this.shadowRoot && this._applyStyles(r), this._mount(e);
		}, t = this._def.__asyncLoader;
		if (t) return this._pendingResolve = t().then((t) => {
			t.configureApp = this._def.configureApp, e(this._def = t, !0);
		}), this._pendingResolve;
		e(this._def);
	}
	_mount(e) {
		__VUE_PROD_DEVTOOLS__ && !e.name && (e.name = "VueElement"), this._app = this._createApp(e), this._inheritParentContext(), e.configureApp && e.configureApp(this._app), this._app._ceVNode = this._createVNode(), this._app.mount(this._root);
		let t = this._instance && this._instance.exposed;
		if (t) for (let e in t) z(this, e) || Object.defineProperty(this, e, { get: () => xa(t[e]) });
	}
	_resolveProps(e) {
		let { props: t } = e, n = B(t) ? t : Object.keys(t || {});
		for (let e of Object.keys(this)) e[0] !== "_" && n.includes(e) && this._setProp(e, this[e]);
		for (let e of n.map(W)) Object.defineProperty(this, e, {
			get() {
				return this._getProp(e);
			},
			set(t) {
				this._setProp(e, t, !0, !this._patching);
			}
		});
	}
	_setAttr(e) {
		if (e.startsWith("data-v-")) return;
		let t = this.hasAttribute(e), n = t ? this.getAttribute(e) : dp, r = W(e);
		t && this._numberProps && this._numberProps[r] && (n = lr(n)), this._setProp(r, n, !1, !0);
	}
	_getProp(e) {
		return this._props[e];
	}
	_setProp(e, t, n = !0, r = !1) {
		if (t !== this._props[e] && (this._dirty = !0, t === dp ? delete this._props[e] : (this._props[e] = t, e === "key" && this._app && (this._app._ceVNode.key = t)), r && this._instance && this._update(), n)) {
			let n = this._ob;
			n && (this._processMutations(n.takeRecords()), n.disconnect()), t === !0 ? this.setAttribute(rr(e), "") : typeof t == "string" || typeof t == "number" ? this.setAttribute(rr(e), t + "") : t || this.removeAttribute(rr(e)), n && n.observe(this, { attributes: !0 });
		}
	}
	_update() {
		let e = this._createVNode();
		this._app && (e.appContext = this._app._context), im(e, this._root);
	}
	_createVNode() {
		let e = {};
		this.shadowRoot || (e.onVnodeMounted = e.onVnodeUpdated = this._renderSlots.bind(this));
		let t = $(this._def, R(e, this._props));
		return this._instance || (t.ce = (e) => {
			this._instance = e, e.ce = this, e.isCE = !0;
			let t = (e, t) => {
				this.dispatchEvent(new CustomEvent(e, Zn(t[0]) ? R({ detail: t }, t[0]) : { detail: t }));
			};
			e.emit = (e, ...n) => {
				t(e, n), rr(e) !== e && t(rr(e), n);
			}, this._setParent();
		}), t;
	}
	_applyStyles(e, t, n) {
		if (!e) return;
		if (t) {
			if (t === this._def || this._styleChildren.has(t)) return;
			this._styleChildren.add(t);
		}
		let r = this._nonce, i = this.shadowRoot, a = n ? this._getStyleAnchor(n) || this._getStyleAnchor(this._def) : this._getRootStyleInsertionAnchor(i), o = null;
		for (let s = e.length - 1; s >= 0; s--) {
			let c = document.createElement("style");
			r && c.setAttribute("nonce", r), c.textContent = e[s], i.insertBefore(c, o || a), o = c, s === 0 && (n || this._styleAnchors.set(this._def, c), t && this._styleAnchors.set(t, c));
		}
	}
	_getStyleAnchor(e) {
		if (!e) return null;
		let t = this._styleAnchors.get(e);
		return t && t.parentNode === this.shadowRoot ? t : (t && this._styleAnchors.delete(e), null);
	}
	_getRootStyleInsertionAnchor(e) {
		for (let t = 0; t < e.childNodes.length; t++) {
			let n = e.childNodes[t];
			if (!(n instanceof HTMLStyleElement)) return n;
		}
		return null;
	}
	_parseSlots() {
		let e = this._slots = {}, t;
		for (; t = this.firstChild;) {
			let n = t.nodeType === 1 && t.getAttribute("slot") || "default";
			(e[n] || (e[n] = [])).push(t), this.removeChild(t);
		}
	}
	_renderSlots() {
		let e = this._getSlots(), t = this._instance.type.__scopeId;
		for (let n = 0; n < e.length; n++) {
			let r = e[n], i = r.getAttribute("name") || "default", a = this._slots[i], o = r.parentNode;
			if (a) for (let e of a) {
				if (t && e.nodeType === 1) {
					let n = t + "-s", r = document.createTreeWalker(e, 1);
					e.setAttribute(n, "");
					let i;
					for (; i = r.nextNode();) i.setAttribute(n, "");
				}
				o.insertBefore(e, r);
			}
			else for (; r.firstChild;) o.insertBefore(r.firstChild, r);
			o.removeChild(r);
		}
	}
	_getSlots() {
		let e = [this];
		this._teleportTargets && e.push(...this._teleportTargets);
		let t = /* @__PURE__ */ new Set();
		for (let n of e) {
			let e = n.querySelectorAll("slot");
			for (let n = 0; n < e.length; n++) t.add(e[n]);
		}
		return Array.from(t);
	}
	_injectChildStyle(e, t) {
		this._applyStyles(e.styles, e, t);
	}
	_beginPatch() {
		this._patching = !0, this._dirty = !1;
	}
	_endPatch() {
		this._patching = !1, this._dirty && this._instance && this._update();
	}
	_hasShadowRoot() {
		return this._def.shadowRoot !== !1;
	}
	_removeChildStyle(e) {}
};
function gp(e) {
	let t = bd();
	return t && t.ce || null;
}
function _p() {
	let e = gp();
	return e && e.shadowRoot;
}
function vp(e = "$style") {
	{
		let t = bd();
		if (!t) return L;
		let n = t.type.__cssModules;
		return n && n[e] || L;
	}
}
var yp = /* @__PURE__ */ new WeakMap(), bp = /* @__PURE__ */ new WeakMap(), xp = /* @__PURE__ */ Symbol("_moveCb"), Sp = /* @__PURE__ */ Symbol("_enterCb"), Cp = /* @__PURE__ */ ((e) => (delete e.props.mode, e))({
	name: "TransitionGroup",
	props: /* @__PURE__ */ R({}, pf, {
		tag: String,
		moveClass: String
	}),
	setup(e, { slots: t }) {
		let n = bd(), r = ys(), i, a;
		return Pc(() => {
			if (!i.length) return;
			let t = e.moveClass || `${e.name || "v"}-move`;
			if (!Op(i[0].el, n.vnode.el, t)) {
				i = [];
				return;
			}
			i.forEach(wp), i.forEach(Tp);
			let r = i.filter(Ep);
			Of(n.vnode.el), r.forEach((e) => {
				let n = e.el, r = n.style;
				bf(n, t), r.transform = r.webkitTransform = r.transitionDuration = "";
				let i = n[xp] = (e) => {
					e && e.target !== n || (!e || e.propertyName.endsWith("transform")) && (n.removeEventListener("transitionend", i), n[xp] = null, xf(n, t));
				};
				n.addEventListener("transitionend", i);
			}), i = [];
		}), () => {
			let o = /* @__PURE__ */ J(e), s = _f(o), c = o.tag || Z;
			if (i = [], a) for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.el && t.el instanceof Element && !t.el[jf] && (i.push(t), As(t, Ds(t, s, r, n)), yp.set(t, Dp(t.el)));
			}
			a = t.default ? js(t.default()) : [];
			for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.key != null && As(t, Ds(t, s, r, n));
			}
			return $(c, null, a);
		};
	}
});
function wp(e) {
	let t = e.el;
	t[xp] && t[xp](), t[Sp] && t[Sp]();
}
function Tp(e) {
	bp.set(e, Dp(e.el));
}
function Ep(e) {
	let t = yp.get(e), n = bp.get(e), r = t.left - n.left, i = t.top - n.top;
	if (r || i) {
		let t = e.el, n = t.style, a = t.getBoundingClientRect(), o = 1, s = 1;
		return t.offsetWidth && (o = a.width / t.offsetWidth), t.offsetHeight && (s = a.height / t.offsetHeight), (!Number.isFinite(o) || o === 0) && (o = 1), (!Number.isFinite(s) || s === 0) && (s = 1), Math.abs(o - 1) < .01 && (o = 1), Math.abs(s - 1) < .01 && (s = 1), n.transform = n.webkitTransform = `translate(${r / o}px,${i / s}px)`, n.transitionDuration = "0s", e;
	}
}
function Dp(e) {
	let t = e.getBoundingClientRect();
	return {
		left: t.left,
		top: t.top
	};
}
function Op(e, t, n) {
	let r = e.cloneNode(), i = e[df];
	i && i.forEach((e) => {
		e.split(/\s+/).forEach((e) => e && r.classList.remove(e));
	}), n.split(/\s+/).forEach((e) => e && r.classList.add(e)), r.style.display = "none";
	let a = t.nodeType === 1 ? t : t.parentNode;
	a.appendChild(r);
	let { hasTransform: o } = Tf(r);
	return a.removeChild(r), o;
}
var kp = (e) => {
	let t = e.props["onUpdate:modelValue"] || !1;
	return B(t) ? (e) => or(t, e) : t;
};
function Ap(e) {
	e.target.composing = !0;
}
function jp(e) {
	let t = e.target;
	t.composing && (t.composing = !1, t.dispatchEvent(new Event("input")));
}
var Mp = /* @__PURE__ */ Symbol("_assign"), Np = /* @__PURE__ */ Symbol("_initialValue");
function Pp(e, t, n) {
	return t && (e = e.trim()), n && (e = cr(e)), e;
}
var Fp = {
	created(e, { modifiers: { lazy: t, trim: n, number: r } }, i) {
		e.parentNode && (e.type === "text" ? e[Np] = e.defaultValue.replace(/[\r\n]/g, "") : e.type === "textarea" && (e[Np] = e.defaultValue.replace(/\r\n?/g, "\n"))), e[Mp] = kp(i);
		let a = r || i.props && i.props.type === "number";
		Xf(e, t ? "change" : "input", (t) => {
			t.target.composing || e[Mp](Pp(e.value, n, a));
		}), (n || a) && Xf(e, "change", () => {
			e.value = Pp(e.value, n, a);
		}), t || (Xf(e, "compositionstart", Ap), Xf(e, "compositionend", jp), Xf(e, "change", jp));
	},
	mounted(e, { value: t, modifiers: { trim: n, number: r } }) {
		let i = t ?? "", a = e[Np];
		delete e[Np], a !== void 0 && (e.type === "text" || e.type === "textarea") && e.value !== a ? e[Mp](Pp(e.value, n, r)) : e.value = i;
	},
	beforeUpdate(e, { value: t, oldValue: n, modifiers: { lazy: r, trim: i, number: a } }, o) {
		if (e[Mp] = kp(o), e.composing) return;
		let s = (a || e.type === "number") && !/^0\d/.test(e.value) ? cr(e.value) : e.value, c = t ?? "";
		if (s === c) return;
		let l = e.getRootNode();
		(l instanceof Document || l instanceof ShadowRoot) && l.activeElement === e && e.type !== "range" && (r && t === n || i && e.value.trim() === c) || (e.value = c);
	}
}, Ip = {
	deep: !0,
	created(e, t, n) {
		e[Mp] = kp(n), Xf(e, "change", () => {
			let t = e._modelValue, n = Hp(e), r = e.checked, i = e[Mp];
			if (B(t)) {
				let e = Fr(t, n), a = e !== -1;
				if (r && !a) i(t.concat(n));
				else if (!r && a) {
					let n = [...t];
					n.splice(e, 1), i(n);
				}
			} else if (Un(t)) {
				let e = new Set(t);
				r ? e.add(n) : e.delete(n), i(e);
			} else i(Up(e, r));
		});
	},
	mounted: Lp,
	beforeUpdate(e, t, n) {
		e[Mp] = kp(n), Lp(e, t, n);
	}
};
function Lp(e, { value: t, oldValue: n }, r) {
	e._modelValue = t;
	let i;
	if (B(t)) i = Fr(t, r.props.value) > -1;
	else if (Un(t)) i = t.has(r.props.value);
	else {
		if (t === n) return;
		i = Pr(t, Up(e, !0));
	}
	e.checked !== i && (e.checked = i);
}
var Rp = {
	created(e, { value: t }, n) {
		e.checked = Pr(t, n.props.value), e[Mp] = kp(n), Xf(e, "change", () => {
			e[Mp](Hp(e));
		});
	},
	beforeUpdate(e, { value: t, oldValue: n }, r) {
		e[Mp] = kp(r), t !== n && (e.checked = Pr(t, r.props.value));
	}
}, zp = {
	deep: !0,
	created(e, { value: t, modifiers: { number: n } }, r) {
		e._modelValue = t, Xf(e, "change", () => {
			let t = Array.prototype.filter.call(e.options, (e) => e.selected).map((e) => n ? cr(Hp(e)) : Hp(e)), r = e.multiple, i = r ? Un(e._modelValue) ? new Set(t) : t : t[0], a = e._pendingValue = [r, r ? B(i) ? t.slice() : t : i];
			try {
				e[Mp](i);
			} finally {
				mo(() => {
					e._pendingValue === a && (e._pendingValue = void 0);
				});
			}
		}), e[Mp] = kp(r);
	},
	mounted(e, { value: t }) {
		Vp(e, t);
	},
	beforeUpdate(e, { value: t }, n) {
		e._modelValue = t, e[Mp] = kp(n);
	},
	updated(e, { value: t }) {
		let n = e._pendingValue;
		e._pendingValue = void 0, (!n || n[0] !== e.multiple || !Bp(t, n[1], n[0])) && Vp(e, t);
	}
};
function Bp(e, t, n) {
	if (!n || B(e)) return Pr(e, t);
	if (Un(e)) {
		if (e.size !== t.length) return !1;
		for (let n of t) if (!e.has(n)) return !1;
		return !0;
	}
	return !1;
}
function Vp(e, t) {
	let n = e.multiple, r = B(t);
	if (!n || r || Un(t)) {
		for (let i = 0, a = e.options.length; i < a; i++) {
			let a = e.options[i], o = Hp(a);
			if (n) {
				if (r) {
					let e = typeof o;
					a.selected = e === "string" || e === "number" ? t.some((e) => String(e) === String(o)) : Fr(t, o) > -1;
				} else a.selected = t.has(o);
			} else if (Pr(Hp(a), t)) {
				e.selectedIndex !== i && (e.selectedIndex = i);
				return;
			}
		}
		!n && e.selectedIndex !== -1 && (e.selectedIndex = -1);
	}
}
function Hp(e) {
	return "_value" in e ? e._value : e.value;
}
function Up(e, t) {
	let n = t ? "_trueValue" : "_falseValue";
	return n in e ? e[n] : t;
}
var Wp = {
	created(e, t, n) {
		Kp(e, t, n, null, "created");
	},
	mounted(e, t, n) {
		Kp(e, t, n, null, "mounted");
	},
	beforeUpdate(e, t, n, r) {
		Kp(e, t, n, r, "beforeUpdate");
	},
	updated(e, t, n, r) {
		Kp(e, t, n, r, "updated");
	}
};
function Gp(e, t) {
	switch (e) {
		case "SELECT": return zp;
		case "TEXTAREA": return Fp;
		default: switch (t) {
			case "checkbox": return Ip;
			case "radio": return Rp;
			default: return Fp;
		}
	}
}
function Kp(e, t, n, r, i) {
	let a = Gp(e.tagName, n.props && n.props.type)[i];
	a && a(e, t, n, r);
}
function qp() {
	Fp.getSSRProps = ({ value: e }) => ({ value: e }), Rp.getSSRProps = ({ value: e }, t) => {
		if (t.props && Pr(t.props.value, e)) return { checked: !0 };
	}, Ip.getSSRProps = ({ value: e }, t) => {
		if (B(e)) {
			if (t.props && Fr(e, t.props.value) > -1) return { checked: !0 };
		} else if (Un(e)) {
			if (t.props && e.has(t.props.value)) return { checked: !0 };
		} else if (e) return { checked: !0 };
	}, Wp.getSSRProps = (e, t) => {
		if (typeof t.type != "string") return;
		let n = Gp(t.type.toUpperCase(), t.props && t.props.type);
		if (n.getSSRProps) return n.getSSRProps(e, t);
	};
}
var Jp = [
	"ctrl",
	"shift",
	"alt",
	"meta"
], Yp = {
	stop: (e) => e.stopPropagation(),
	prevent: (e) => e.preventDefault(),
	self: (e) => e.target !== e.currentTarget,
	ctrl: (e) => !e.ctrlKey,
	shift: (e) => !e.shiftKey,
	alt: (e) => !e.altKey,
	meta: (e) => !e.metaKey,
	left: (e) => "button" in e && e.button !== 0,
	middle: (e) => "button" in e && e.button !== 1,
	right: (e) => "button" in e && e.button !== 2,
	exact: (e, t) => Jp.some((n) => e[`${n}Key`] && !t.includes(n))
}, Xp = (e, t) => {
	if (!e) return e;
	let n = e._withMods || (e._withMods = {}), r = t.join(".");
	return n[r] || (n[r] = ((n, ...r) => {
		for (let e = 0; e < t.length; e++) {
			let r = Yp[t[e]];
			if (r && r(n, t)) return;
		}
		return e(n, ...r);
	}));
}, Zp = {
	esc: "escape",
	space: " ",
	up: "arrow-up",
	left: "arrow-left",
	right: "arrow-right",
	down: "arrow-down",
	delete: "backspace"
}, Qp = (e, t) => {
	let n = e._withKeys || (e._withKeys = {}), r = t.join(".");
	return n[r] || (n[r] = ((n) => {
		if (!("key" in n)) return;
		let r = rr(n.key);
		if (t.some((e) => e === r || Zp[e] === r)) return e(n);
	}));
}, $p = /* @__PURE__ */ R({ patchProp: cp }, cf), em, tm = !1;
function nm() {
	return em || (em = vu($p));
}
function rm() {
	return em = tm ? em : yu($p), tm = !0, em;
}
var im = ((...e) => {
	nm().render(...e);
}), am = ((...e) => {
	rm().hydrate(...e);
}), om = ((...e) => {
	let t = nm().createApp(...e), { mount: n } = t;
	return t.mount = (e) => {
		let r = lm(e);
		if (!r) return;
		let i = t._component;
		!V(i) && !i.render && !i.template && (i.template = r.innerHTML), r.nodeType === 1 && (r.textContent = "");
		let a = n(r, !1, cm(r));
		return r instanceof Element && (r.removeAttribute("v-cloak"), r.setAttribute("data-v-app", "")), a;
	}, t;
}), sm = ((...e) => {
	let t = rm().createApp(...e), { mount: n } = t;
	return t.mount = (e) => {
		let t = lm(e);
		if (t) return n(t, !0, cm(t));
	}, t;
});
function cm(e) {
	if (e instanceof SVGElement) return "svg";
	if (typeof MathMLElement == "function" && e instanceof MathMLElement) return "mathml";
}
function lm(e) {
	return H(e) ? document.querySelector(e) : e;
}
var um = !1, dm = () => {
	um || (um = !0, qp(), Pf());
}, fm = /* @__PURE__ */ c({
	BaseTransition: () => Ts,
	BaseTransitionPropsValidators: () => xs,
	Comment: () => Q,
	DeprecationTypes: () => null,
	EffectScope: () => Vr,
	ErrorCodes: () => eo,
	ErrorTypeStrings: () => Xd,
	Fragment: () => Z,
	KeepAlive: () => xc,
	ReactiveEffect: () => Kr,
	Static: () => Uu,
	Suspense: () => ju,
	Teleport: () => ms,
	Text: () => Hu,
	TrackOpTypes: () => Pa,
	Transition: () => mf,
	TransitionGroup: () => Cp,
	TriggerOpTypes: () => Fa,
	VueElement: () => hp,
	assertNumber: () => $a,
	callWithAsyncErrorHandling: () => ro,
	callWithErrorHandling: () => no,
	camelize: () => W,
	capitalize: () => ir,
	cloneVNode: () => sd,
	compatUtils: () => null,
	compile: () => pm,
	computed: () => Ud,
	createApp: () => om,
	createBlock: () => Qu,
	createCommentVNode: () => ud,
	createElementBlock: () => Zu,
	createElementVNode: () => id,
	createHydrationRenderer: () => yu,
	createPropsRestProxy: () => vl,
	createRenderer: () => vu,
	createSSRApp: () => sm,
	createSlots: () => Xc,
	createStaticVNode: () => ld,
	createTextVNode: () => cd,
	createVNode: () => $,
	customRef: () => Ea,
	defineAsyncComponent: () => vc,
	defineComponent: () => Ms,
	defineCustomElement: () => fp,
	defineEmits: () => ol,
	defineExpose: () => sl,
	defineModel: () => ul,
	defineOptions: () => cl,
	defineProps: () => al,
	defineSSRCustomElement: () => pp,
	defineSlots: () => ll,
	devtools: () => Zd,
	effect: () => ai,
	effectScope: () => Hr,
	getCurrentInstance: () => bd,
	getCurrentScope: () => Ur,
	getCurrentWatcher: () => za,
	getTransitionRawChildren: () => js,
	guardReactiveProps: () => od,
	h: () => Wd,
	handleError: () => io,
	hasInjectionContext: () => qo,
	hydrate: () => am,
	hydrateOnIdle: () => dc,
	hydrateOnInteraction: () => hc,
	hydrateOnMediaQuery: () => mc,
	hydrateOnVisible: () => pc,
	initCustomFormatter: () => Gd,
	initDirectivesForSSR: () => dm,
	inject: () => Ko,
	isMemoSame: () => qd,
	isProxy: () => fa,
	isReactive: () => la,
	isReadonly: () => ua,
	isRef: () => Y,
	isRuntimeOnly: () => Nd,
	isShallow: () => da,
	isVNode: () => $u,
	markRaw: () => pa,
	mergeDefaults: () => gl,
	mergeModels: () => _l,
	mergeProps: () => md,
	nextTick: () => mo,
	nodeOps: () => cf,
	normalizeClass: () => yr,
	normalizeProps: () => br,
	normalizeStyle: () => pr,
	onActivated: () => Cc,
	onBeforeMount: () => jc,
	onBeforeUnmount: () => Fc,
	onBeforeUpdate: () => Nc,
	onDeactivated: () => wc,
	onErrorCaptured: () => Bc,
	onMounted: () => Mc,
	onRenderTracked: () => zc,
	onRenderTriggered: () => Rc,
	onScopeDispose: () => Wr,
	onServerPrefetch: () => Lc,
	onUnmounted: () => Ic,
	onUpdated: () => Pc,
	onWatcherCleanup: () => Ba,
	openBlock: () => Ku,
	patchProp: () => cp,
	popScopeId: () => Bo,
	provide: () => Go,
	proxyRefs: () => wa,
	pushScopeId: () => zo,
	queuePostFlushCb: () => vo,
	reactive: () => ia,
	readonly: () => oa,
	ref: () => ga,
	registerRuntimeCompiler: () => Md,
	render: () => im,
	renderList: () => Yc,
	renderSlot: () => Zc,
	resolveComponent: () => Uc,
	resolveDirective: () => Kc,
	resolveDynamicComponent: () => Gc,
	resolveFilter: () => null,
	resolveTransitionHooks: () => Ds,
	setBlockTracking: () => Yu,
	setDevtoolsHook: () => Qd,
	setTransitionHooks: () => As,
	shallowReactive: () => aa,
	shallowReadonly: () => sa,
	shallowRef: () => _a,
	ssrContextKey: () => Jo,
	ssrUtils: () => $d,
	stop: () => oi,
	toDisplayString: () => Lr,
	toHandlerKey: () => ar,
	toHandlers: () => $c,
	toRaw: () => J,
	toRef: () => Aa,
	toRefs: () => Da,
	toValue: () => Sa,
	transformVNodeArgs: () => td,
	triggerRef: () => ba,
	unref: () => xa,
	useAttrs: () => pl,
	useCssModule: () => vp,
	useCssVars: () => If,
	useHost: () => gp,
	useId: () => Ns,
	useModel: () => zl,
	useSSRContext: () => Yo,
	useShadowRoot: () => _p,
	useSlots: () => fl,
	useTemplateRef: () => Fs,
	useTransitionState: () => ys,
	vModelCheckbox: () => Ip,
	vModelDynamic: () => Wp,
	vModelRadio: () => Rp,
	vModelSelect: () => zp,
	vModelText: () => Fp,
	vShow: () => Mf,
	version: () => Jd,
	warn: () => Yd,
	watch: () => $o,
	watchEffect: () => Xo,
	watchPostEffect: () => Zo,
	watchSyncEffect: () => Qo,
	withAsyncContext: () => yl,
	withCtx: () => Ho,
	withDefaults: () => dl,
	withDirectives: () => Uo,
	withKeys: () => Qp,
	withMemo: () => Kd,
	withModifiers: () => Xp,
	withScopeId: () => Vo
}), pm = () => {};
//#endregion
//#region node_modules/vue-i18n/node_modules/@vue/devtools-api/lib/esm/env.js
function mm() {
	return hm().__VUE_DEVTOOLS_GLOBAL_HOOK__;
}
function hm() {
	return typeof navigator < "u" && typeof window < "u" ? window : typeof globalThis < "u" ? globalThis : {};
}
var gm = typeof Proxy == "function", _m = "devtools-plugin:setup", vm = "plugin:settings:set", ym, bm;
function xm() {
	return ym === void 0 && (typeof window < "u" && window.performance ? (ym = !0, bm = window.performance) : typeof globalThis < "u" && globalThis.perf_hooks?.performance ? (ym = !0, bm = globalThis.perf_hooks.performance) : ym = !1), ym;
}
function Sm() {
	return xm() ? bm.now() : Date.now();
}
//#endregion
//#region node_modules/vue-i18n/node_modules/@vue/devtools-api/lib/esm/proxy.js
var Cm = class {
	constructor(e, t) {
		this.target = null, this.targetQueue = [], this.onQueue = [], this.plugin = e, this.hook = t;
		let n = {};
		if (e.settings) for (let t in e.settings) n[t] = e.settings[t].defaultValue;
		let r = `__vue-devtools-plugin-settings__${e.id}`, i = Object.assign({}, n);
		try {
			let e = localStorage.getItem(r), t = JSON.parse(e);
			Object.assign(i, t);
		} catch {}
		this.fallbacks = {
			getSettings() {
				return i;
			},
			setSettings(e) {
				try {
					localStorage.setItem(r, JSON.stringify(e));
				} catch {}
				i = e;
			},
			now() {
				return Sm();
			}
		}, t && t.on(vm, (e, t) => {
			e === this.plugin.id && this.fallbacks.setSettings(t);
		}), this.proxiedOn = new Proxy({}, { get: (e, t) => this.target ? this.target.on[t] : (...e) => {
			this.onQueue.push({
				method: t,
				args: e
			});
		} }), this.proxiedTarget = new Proxy({}, { get: (e, t) => this.target ? this.target[t] : t === "on" ? this.proxiedOn : Object.keys(this.fallbacks).includes(t) ? (...e) => (this.targetQueue.push({
			method: t,
			args: e,
			resolve: () => {}
		}), this.fallbacks[t](...e)) : (...e) => new Promise((n) => {
			this.targetQueue.push({
				method: t,
				args: e,
				resolve: n
			});
		}) });
	}
	async setRealTarget(e) {
		this.target = e;
		for (let e of this.onQueue) this.target.on[e.method](...e.args);
		for (let e of this.targetQueue) e.resolve(await this.target[e.method](...e.args));
	}
};
//#endregion
//#region node_modules/vue-i18n/node_modules/@vue/devtools-api/lib/esm/index.js
function wm(e, t) {
	let n = e, r = hm(), i = mm(), a = gm && n.enableEarlyProxy;
	if (i && (r.__VUE_DEVTOOLS_PLUGIN_API_AVAILABLE__ || !a)) i.emit(_m, e, t);
	else {
		let e = a ? new Cm(n, i) : null;
		(r.__VUE_DEVTOOLS_PLUGINS__ = r.__VUE_DEVTOOLS_PLUGINS__ || []).push({
			pluginDescriptor: n,
			setupFn: t,
			proxy: e
		}), e && t(e.proxiedTarget);
	}
}
//#endregion
//#region node_modules/vue-i18n/dist/vue-i18n.mjs
var Tm = "11.3.2";
function Em() {
	typeof __INTLIFY_DROP_MESSAGE_COMPILER__ != "boolean" && (w().__INTLIFY_DROP_MESSAGE_COMPILER__ = !1);
}
var Dm = {
	UNEXPECTED_RETURN_TYPE: 24,
	INVALID_ARGUMENT: 25,
	MUST_BE_CALL_SETUP_TOP: 26,
	NOT_INSTALLED: 27,
	REQUIRED_VALUE: 28,
	INVALID_VALUE: 29,
	CANNOT_SETUP_VUE_DEVTOOLS_PLUGIN: 30,
	NOT_INSTALLED_WITH_PROVIDE: 31,
	UNEXPECTED_ERROR: 32,
	NOT_COMPATIBLE_LEGACY_VUE_I18N: 33,
	NOT_AVAILABLE_COMPOSITION_IN_LEGACY: 34
};
function Om(e, ...t) {
	return de(e, null, void 0);
}
Dm.UNEXPECTED_RETURN_TYPE, Dm.INVALID_ARGUMENT, Dm.MUST_BE_CALL_SETUP_TOP, Dm.NOT_INSTALLED, Dm.UNEXPECTED_ERROR, Dm.REQUIRED_VALUE, Dm.INVALID_VALUE, Dm.CANNOT_SETUP_VUE_DEVTOOLS_PLUGIN, Dm.NOT_INSTALLED_WITH_PROVIDE, Dm.NOT_COMPATIBLE_LEGACY_VUE_I18N, Dm.NOT_AVAILABLE_COMPOSITION_IN_LEGACY;
var km = /* #__PURE__*/ p("__translateVNode"), Am = /* #__PURE__*/ p("__datetimeParts"), jm = /* #__PURE__*/ p("__numberParts"), Mm = /* #__PURE__*/ p("__enableEmitter"), Nm = p("__setPluralRules");
p("__intlifyMeta");
var Pm = /* #__PURE__*/ p("__injectWithOption"), Fm = {
	FALLBACK_TO_ROOT: 10,
	NOT_FOUND_PARENT_SCOPE: 11,
	IGNORE_OBJ_FLATTEN: 12,
	DEPRECATE_LEGACY_MODE: 13,
	DEPRECATE_TRANSLATE_CUSTOME_DIRECTIVE: 14,
	DUPLICATE_USE_I18N_CALLING: 15
};
Fm.FALLBACK_TO_ROOT, Fm.NOT_FOUND_PARENT_SCOPE, Fm.IGNORE_OBJ_FLATTEN, Fm.DEPRECATE_LEGACY_MODE, Fm.DEPRECATE_TRANSLATE_CUSTOME_DIRECTIVE, Fm.DUPLICATE_USE_I18N_CALLING;
function Im(e) {
	if (!N(e) || Ge(e)) return e;
	for (let t in e) if (O(e, t)) {
		if (!t.includes(".")) N(e[t]) && Im(e[t]);
		else {
			let n = t.split("."), r = n.length - 1, i = e, a = !1;
			for (let e = 0; e < r; e++) {
				if (n[e] === "__proto__") throw Error(`unsafe key: ${n[e]}`);
				if (n[e] in i || (i[n[e]] = S()), !N(i[n[e]])) {
					a = !0;
					break;
				}
				i = i[n[e]];
			}
			if (a || (Ge(i) ? lt.includes(n[r]) || delete e[t] : (i[n[r]] = e[t], delete e[t])), !Ge(i)) {
				let e = i[n[r]];
				N(e) && Im(e);
			}
		}
	}
	return e;
}
function Lm(e, t) {
	let { messages: n, __i18n: r, messageResolver: i, flatJson: a } = t, o = P(n) ? n : k(r) ? S() : { [e]: S() };
	if (k(r) && r.forEach((e) => {
		if ("locale" in e && "resource" in e) {
			let { locale: t, resource: n } = e;
			t ? (o[t] = o[t] || S(), ce(n, o[t])) : ce(n, o);
		} else j(e) && ce(JSON.parse(e), o);
	}), i == null && a) for (let e in o) O(o, e) && Im(o[e]);
	return o;
}
function Rm(e) {
	return $(Hu, null, e, 0);
}
function zm() {
	let e = "currentInstance";
	return e in fm ? fm[e] : bd();
}
var Bm = () => [], Vm = () => !1, Hm = 0;
function Um(e) {
	return ((t, n, r, i) => e(n, r, zm() || void 0, i));
}
function Wm(e = {}) {
	let { __root: t, __injectWithOption: n } = e, r = t === void 0, i = e.flatJson, a = f ? ga : _a, o = !M(e.inheritLocale) || e.inheritLocale, s = a(t && o ? t.locale.value : j(e.locale) ? e.locale : Vt), c = a(t && o ? t.fallbackLocale.value : j(e.fallbackLocale) || k(e.fallbackLocale) || P(e.fallbackLocale) || e.fallbackLocale === !1 ? e.fallbackLocale : s.value), l = a(Lm(s.value, e)), u = a(P(e.datetimeFormats) ? e.datetimeFormats : { [s.value]: {} }), d = a(P(e.numberFormats) ? e.numberFormats : { [s.value]: {} }), p = t ? t.missingWarn : M(e.missingWarn) || v(e.missingWarn) ? e.missingWarn : !0, m = t ? t.fallbackWarn : M(e.fallbackWarn) || v(e.fallbackWarn) ? e.fallbackWarn : !0, h = t ? t.fallbackRoot : !M(e.fallbackRoot) || e.fallbackRoot, _ = !!e.fallbackFormat, y = A(e.missing) ? e.missing : null, x = A(e.missing) ? Um(e.missing) : null, S = A(e.postTranslation) ? e.postTranslation : null, C = t ? t.warnHtmlMessage : !M(e.warnHtmlMessage) || e.warnHtmlMessage, w = !!e.escapeParameter, T = t ? t.modifiers : P(e.modifiers) ? e.modifiers : {}, E = e.pluralRules || t && t.pluralRules, D;
	D = (() => {
		r && Zt(null);
		let t = {
			version: Tm,
			locale: s.value,
			fallbackLocale: c.value,
			messages: l.value,
			modifiers: T,
			pluralRules: E,
			missing: x === null ? void 0 : x,
			missingWarn: p,
			fallbackWarn: m,
			fallbackFormat: _,
			unresolving: !0,
			postTranslation: S === null ? void 0 : S,
			warnHtmlMessage: C,
			escapeParameter: w,
			messageResolver: e.messageResolver,
			messageCompiler: e.messageCompiler,
			__meta: { framework: "vue" }
		};
		t.datetimeFormats = u.value, t.numberFormats = d.value, t.__datetimeFormatters = P(D) ? D.__datetimeFormatters : void 0, t.__numberFormatters = P(D) ? D.__numberFormatters : void 0;
		let n = en(t);
		return r && Zt(n), n;
	})(), rn(D, s.value, c.value);
	function ee() {
		return [
			s.value,
			c.value,
			l.value,
			u.value,
			d.value
		];
	}
	let te = Ud({
		get: () => s.value,
		set: (e) => {
			D.locale = e, s.value = e;
		}
	}), ne = Ud({
		get: () => c.value,
		set: (e) => {
			D.fallbackLocale = e, c.value = e, rn(D, s.value, e);
		}
	}), re = Ud(() => l.value), ie = /* #__PURE__*/ Ud(() => u.value), ae = /* #__PURE__*/ Ud(() => d.value);
	function oe() {
		return A(S) ? S : null;
	}
	function se(e) {
		S = e, D.postTranslation = e;
	}
	function le() {
		return y;
	}
	function ue(e) {
		e !== null && (x = Um(e)), y = e, D.missing = x;
	}
	let F = (e, n, i, a, o, s) => {
		ee();
		let c;
		try {
			r || (D.fallbackContext = t ? Qt() : void 0), c = e(D);
		} finally {
			r || (D.fallbackContext = void 0);
		}
		if (i !== "translate exists" && g(c) && c === -1 || i === "translate exists" && !c) {
			let [e, r] = n();
			return t && h ? a(t) : o(e);
		}
		if (s(c)) return c;
		/* istanbul ignore next */
		throw Om(Dm.UNEXPECTED_RETURN_TYPE);
	};
	function de(...e) {
		return F((t) => Reflect.apply(En, null, [t, ...e]), () => jn(...e), "translate", (t) => Reflect.apply(t.t, t, [...e]), (e) => e, (e) => j(e));
	}
	function fe(...e) {
		let [t, n, r] = e;
		if (r && !N(r)) throw Om(Dm.INVALID_ARGUMENT);
		return de(t, n, b({ resolvedMessage: !0 }, r || {}));
	}
	function pe(...e) {
		return F((t) => Reflect.apply(cn, null, [t, ...e]), () => un(...e), "datetime format", (t) => Reflect.apply(t.d, t, [...e]), () => "", (e) => j(e) || k(e));
	}
	function me(...e) {
		return F((t) => Reflect.apply(fn, null, [t, ...e]), () => mn(...e), "number format", (t) => Reflect.apply(t.n, t, [...e]), () => "", (e) => j(e) || k(e));
	}
	function he(e) {
		return e.map((e) => j(e) || g(e) || M(e) ? Rm(String(e)) : e);
	}
	let ge = {
		normalize: he,
		interpolate: (e) => e,
		type: "vnode"
	};
	function _e(...e) {
		return F((t) => {
			let n, r = t;
			try {
				r.processor = ge, n = Reflect.apply(En, null, [r, ...e]);
			} finally {
				r.processor = null;
			}
			return n;
		}, () => jn(...e), "translate", (t) => t[km](...e), (e) => [Rm(e)], (e) => k(e));
	}
	function ve(...e) {
		return F((t) => Reflect.apply(fn, null, [t, ...e]), () => mn(...e), "number format", (t) => t[jm](...e), Bm, (e) => j(e) || k(e));
	}
	function ye(...e) {
		return F((t) => Reflect.apply(cn, null, [t, ...e]), () => un(...e), "datetime format", (t) => t[Am](...e), Bm, (e) => j(e) || k(e));
	}
	function be(e) {
		E = e, D.pluralRules = E;
	}
	function xe(e, t) {
		return F(() => {
			if (!e) return !1;
			let n = j(t) ? t : s.value, r = j(t) ? [n] : Tt(D, c.value, n);
			for (let t = 0; t < r.length; t++) {
				let n = we(r[t]), i = D.messageResolver(n, e);
				if (i === null && (i = n[e]), Ge(i) || Tn(i) || j(i)) return !0;
			}
			return !1;
		}, () => [e], "translate exists", (n) => Reflect.apply(n.te, n, [e, t]), Vm, (e) => M(e));
	}
	function Se(e) {
		let t = null, n = Tt(D, c.value, s.value);
		for (let r = 0; r < n.length; r++) {
			let i = l.value[n[r]] || {}, a = D.messageResolver(i, e);
			if (a != null) {
				t = a;
				break;
			}
		}
		return t;
	}
	function Ce(e) {
		return Se(e) ?? (t && t.tm(e) || {});
	}
	function we(e) {
		return l.value[e] || {};
	}
	function Te(e, t) {
		if (i) {
			let n = { [e]: t };
			for (let e in n) O(n, e) && Im(n[e]);
			t = n[e];
		}
		l.value[e] = t, D.messages = l.value;
	}
	function Ee(e, t) {
		l.value[e] = l.value[e] || {};
		let n = { [e]: t };
		if (i) for (let e in n) O(n, e) && Im(n[e]);
		t = n[e], ce(t, l.value[e]), D.messages = l.value;
	}
	function De(e) {
		return u.value[e] || {};
	}
	function Oe(e, t) {
		u.value[e] = t, D.datetimeFormats = u.value, dn(D, e, t);
	}
	function ke(e, t) {
		u.value[e] = b(u.value[e] || {}, t), D.datetimeFormats = u.value, dn(D, e, t);
	}
	function Ae(e) {
		return d.value[e] || {};
	}
	function je(e, t) {
		d.value[e] = t, D.numberFormats = d.value, hn(D, e, t);
	}
	function Me(e, t) {
		d.value[e] = b(d.value[e] || {}, t), D.numberFormats = d.value, hn(D, e, t);
	}
	Hm++, t && f && ($o(t.locale, (e) => {
		o && (s.value = e, D.locale = e, rn(D, s.value, c.value));
	}), $o(t.fallbackLocale, (e) => {
		o && (c.value = e, D.fallbackLocale = e, rn(D, s.value, c.value));
	}));
	let I = {
		id: Hm,
		locale: te,
		fallbackLocale: ne,
		get inheritLocale() {
			return o;
		},
		set inheritLocale(e) {
			o = e, e && t && (s.value = t.locale.value, c.value = t.fallbackLocale.value, rn(D, s.value, c.value));
		},
		get availableLocales() {
			return Object.keys(l.value).sort();
		},
		messages: re,
		get modifiers() {
			return T;
		},
		get pluralRules() {
			return E || {};
		},
		get isGlobal() {
			return r;
		},
		get missingWarn() {
			return p;
		},
		set missingWarn(e) {
			p = e, D.missingWarn = p;
		},
		get fallbackWarn() {
			return m;
		},
		set fallbackWarn(e) {
			m = e, D.fallbackWarn = m;
		},
		get fallbackRoot() {
			return h;
		},
		set fallbackRoot(e) {
			h = e;
		},
		get fallbackFormat() {
			return _;
		},
		set fallbackFormat(e) {
			_ = e, D.fallbackFormat = _;
		},
		get warnHtmlMessage() {
			return C;
		},
		set warnHtmlMessage(e) {
			C = e, D.warnHtmlMessage = e;
		},
		get escapeParameter() {
			return w;
		},
		set escapeParameter(e) {
			w = e, D.escapeParameter = e;
		},
		t: de,
		getLocaleMessage: we,
		setLocaleMessage: Te,
		mergeLocaleMessage: Ee,
		getPostTranslationHandler: oe,
		setPostTranslationHandler: se,
		getMissingHandler: le,
		setMissingHandler: ue,
		[Nm]: be
	};
	return I.datetimeFormats = ie, I.numberFormats = ae, I.rt = fe, I.te = xe, I.tm = Ce, I.d = pe, I.n = me, I.getDateTimeFormat = De, I.setDateTimeFormat = Oe, I.mergeDateTimeFormat = ke, I.getNumberFormat = Ae, I.setNumberFormat = je, I.mergeNumberFormat = Me, I[Pm] = n, I[km] = _e, I[Am] = ye, I[jm] = ve, I;
}
var Gm = "vue-i18n: composer properties", Km = {
	"vue-devtools-plugin-vue-i18n": "Vue I18n DevTools",
	"vue-i18n-resource-inspector": "Vue I18n DevTools",
	"vue-i18n-timeline": "Vue I18n"
}, qm = { "vue-i18n-resource-inspector": "Search for scopes ..." }, Jm = { "vue-i18n-timeline": 16764185 }, Ym;
async function Xm(e, t) {
	return new Promise((n, r) => {
		try {
			wm({
				id: "vue-devtools-plugin-vue-i18n",
				label: Km["vue-devtools-plugin-vue-i18n"],
				packageName: "vue-i18n",
				homepage: "https://vue-i18n.intlify.dev",
				logo: "https://vue-i18n.intlify.dev/vue-i18n-devtools-logo.png",
				componentStateTypes: [Gm],
				app: e
			}, (r) => {
				Ym = r, r.on.visitComponentTree(({ componentInstance: e, treeNode: n }) => {
					Qm(e, n, t);
				}), r.on.inspectComponent(({ componentInstance: e, instanceData: n }) => {
					e.__VUE_I18N__ && n && (t.mode === "legacy" ? e.__VUE_I18N__ !== t.global.__composer && $m(n, e.__VUE_I18N__) : $m(n, e.__VUE_I18N__));
				}), r.addInspector({
					id: "vue-i18n-resource-inspector",
					label: Km["vue-i18n-resource-inspector"],
					icon: "language",
					treeFilterPlaceholder: qm["vue-i18n-resource-inspector"]
				}), r.on.getInspectorTree((n) => {
					n.app === e && n.inspectorId === "vue-i18n-resource-inspector" && ah(n, t);
				});
				let i = /* @__PURE__ */ new Map();
				r.on.getInspectorState(async (n) => {
					if (n.app === e && n.inspectorId === "vue-i18n-resource-inspector") {
						if (r.unhighlightElement(), ch(n, t), n.nodeId === "global") {
							if (!i.has(n.app)) {
								let [e] = await r.getComponentInstances(n.app);
								i.set(n.app, e);
							}
							r.highlightElement(i.get(n.app));
						} else {
							let e = oh(n.nodeId, t);
							e && r.highlightElement(e);
						}
					}
				}), r.on.editInspectorState((n) => {
					n.app === e && n.inspectorId === "vue-i18n-resource-inspector" && dh(n, t);
				}), r.addTimelineLayer({
					id: "vue-i18n-timeline",
					label: Km["vue-i18n-timeline"],
					color: Jm["vue-i18n-timeline"]
				}), n(!0);
			});
		} catch (e) {
			console.error(e), r(!1);
		}
	});
}
function Zm(e) {
	return e.type.name || e.type.displayName || e.type.__file || "Anonymous";
}
function Qm(e, t, n) {
	let r = n.mode === "composition" ? n.global : n.global.__composer;
	if (e && e.__VUE_I18N__ && e.__VUE_I18N__ !== r) {
		let n = {
			label: `i18n (${Zm(e)} Scope)`,
			textColor: 0,
			backgroundColor: 16764185
		};
		t.tags.push(n);
	}
}
function $m(e, t) {
	let n = Gm;
	e.state.push({
		type: n,
		key: "locale",
		editable: !0,
		value: t.locale.value
	}), e.state.push({
		type: n,
		key: "availableLocales",
		editable: !1,
		value: t.availableLocales
	}), e.state.push({
		type: n,
		key: "fallbackLocale",
		editable: !0,
		value: t.fallbackLocale.value
	}), e.state.push({
		type: n,
		key: "inheritLocale",
		editable: !0,
		value: t.inheritLocale
	}), e.state.push({
		type: n,
		key: "messages",
		editable: !1,
		value: eh(t.messages.value)
	}), e.state.push({
		type: n,
		key: "datetimeFormats",
		editable: !1,
		value: t.datetimeFormats.value
	}), e.state.push({
		type: n,
		key: "numberFormats",
		editable: !1,
		value: t.numberFormats.value
	});
}
function eh(e) {
	let t = {};
	return Object.keys(e).forEach((n) => {
		let r = e[n];
		A(r) && "source" in r ? t[n] = ih(r) : Ge(r) && r.loc && r.loc.source ? t[n] = r.loc.source : N(r) ? t[n] = eh(r) : t[n] = r;
	}), t;
}
var th = {
	"<": "&lt;",
	">": "&gt;",
	"\"": "&quot;",
	"&": "&amp;"
};
function nh(e) {
	return e.replace(/[<>"&]/g, rh);
}
function rh(e) {
	return th[e] || e;
}
function ih(e) {
	return { _custom: {
		type: "function",
		display: `<span>ƒ</span> ${e.source ? `("${nh(e.source)}")` : "(?)"}`
	} };
}
function ah(e, t) {
	e.rootNodes.push({
		id: "global",
		label: "Global Scope"
	});
	let n = t.mode === "composition" ? t.global : t.global.__composer;
	for (let [r, i] of t.__instances) {
		let a = t.mode === "composition" ? i : i.__composer;
		n !== a && e.rootNodes.push({
			id: a.id.toString(),
			label: `${Zm(r)} Scope`
		});
	}
}
function oh(e, t) {
	let n = null;
	if (e !== "global") {
		for (let [r, i] of t.__instances.entries()) if (i.id.toString() === e) {
			n = r;
			break;
		}
	}
	return n;
}
function sh(e, t) {
	if (e === "global") return t.mode === "composition" ? t.global : t.global.__composer;
	{
		let n = Array.from(t.__instances.values()).find((t) => t.id.toString() === e);
		return n ? t.mode === "composition" ? n : n.__composer : null;
	}
}
function ch(e, t) {
	let n = sh(e.nodeId, t);
	return n && (e.state = lh(n)), null;
}
function lh(e) {
	let t = {}, n = "Locale related info";
	t[n] = [
		{
			type: n,
			key: "locale",
			editable: !0,
			value: e.locale.value
		},
		{
			type: n,
			key: "fallbackLocale",
			editable: !0,
			value: e.fallbackLocale.value
		},
		{
			type: n,
			key: "availableLocales",
			editable: !1,
			value: e.availableLocales
		},
		{
			type: n,
			key: "inheritLocale",
			editable: !0,
			value: e.inheritLocale
		}
	];
	let r = "Locale messages info";
	t[r] = [{
		type: r,
		key: "messages",
		editable: !1,
		value: eh(e.messages.value)
	}];
	{
		let n = "Datetime formats info";
		t[n] = [{
			type: n,
			key: "datetimeFormats",
			editable: !1,
			value: e.datetimeFormats.value
		}];
		let r = "Datetime formats info";
		t[r] = [{
			type: r,
			key: "numberFormats",
			editable: !1,
			value: e.numberFormats.value
		}];
	}
	return t;
}
function uh(e, t) {
	if (Ym) {
		let n;
		t && "groupId" in t && (n = t.groupId, delete t.groupId), Ym.addTimelineEvent({
			layerId: "vue-i18n-timeline",
			event: {
				title: e,
				groupId: n,
				time: Date.now(),
				meta: {},
				data: t || {},
				logType: e === "compile-error" ? "error" : e === "fallback" || e === "missing" ? "warning" : "default"
			}
		});
	}
}
function dh(e, t) {
	let n = sh(e.nodeId, t);
	if (n) {
		let [t] = e.path;
		t === "locale" && j(e.state.value) ? n.locale.value = e.state.value : t === "fallbackLocale" && (j(e.state.value) || k(e.state.value) || N(e.state.value)) ? n.fallbackLocale.value = e.state.value : t === "inheritLocale" && M(e.state.value) && (n.inheritLocale = e.state.value);
	}
}
var fh = {
	tag: { type: [String, Object] },
	locale: { type: String },
	scope: {
		type: String,
		validator: (e) => e === "parent" || e === "global",
		default: "parent"
	},
	i18n: { type: Object }
};
b({
	keypath: {
		type: String,
		required: !0
	},
	plural: {
		type: [Number, String],
		validator: (e) => g(e) || !isNaN(e)
	}
}, fh), b({
	value: {
		type: Number,
		required: !0
	},
	format: { type: [String, Object] }
}, fh);
function ph(e = {}) {
	let t = !M(e.globalInjection) || e.globalInjection, n = /* @__PURE__ */ new Map(), [r, i] = mh(e, !1), a = /* #__PURE__*/ p("");
	function o(e) {
		return n.get(e) || null;
	}
	function s(e, t) {
		n.set(e, t);
	}
	function c(e) {
		n.delete(e);
	}
	let l = {
		get mode() {
			return "composition";
		},
		async install(e, ...n) {
			if (__VUE_PROD_DEVTOOLS__ && (e.__VUE_I18N__ = l), e.__VUE_I18N_SYMBOL__ = a, e.provide(e.__VUE_I18N_SYMBOL__, l), P(n[0])) {
				let e = n[0];
				l.__composerExtend = e.__composerExtend, l.__vueI18nExtend = e.__vueI18nExtend;
			}
			let r = null;
			t && (r = _h(e, l.global));
			let o = e.unmount;
			if (e.unmount = () => {
				r && r(), l.dispose(), o();
			}, __VUE_PROD_DEVTOOLS__) {
				if (!await Xm(e, l)) throw Om(Dm.CANNOT_SETUP_VUE_DEVTOOLS_PLUGIN);
				let t = oe();
				{
					let e = i;
					e[Mm] && e[Mm](t);
				}
				t.on("*", uh);
			}
		},
		get global() {
			return i;
		},
		dispose() {
			r.stop();
		},
		__instances: n,
		__getInstance: o,
		__setInstance: s,
		__deleteInstance: c
	};
	return l;
}
function mh(e, t) {
	let n = Hr(), r = n.run(() => Wm(e));
	if (r == null) throw Om(Dm.UNEXPECTED_ERROR);
	return [n, r];
}
var hh = [
	"locale",
	"fallbackLocale",
	"availableLocales"
], gh = [
	"t",
	"rt",
	"d",
	"n",
	"tm",
	"te"
];
function _h(e, t) {
	let n = Object.create(null);
	return hh.forEach((e) => {
		let r = Object.getOwnPropertyDescriptor(t, e);
		if (!r) throw Om(Dm.UNEXPECTED_ERROR);
		let i = /* @__PURE__ */ Y(r.value) ? {
			get() {
				return r.value.value;
			},
			set(e) {
				r.value.value = e;
			}
		} : { get() {
			return r.get && r.get();
		} };
		Object.defineProperty(n, e, i);
	}), e.config.globalProperties.$i18n = n, gh.forEach((n) => {
		let r = Object.getOwnPropertyDescriptor(t, n);
		if (!r || !r.value) throw Om(Dm.UNEXPECTED_ERROR);
		Object.defineProperty(e.config.globalProperties, `$${n}`, r);
	}), () => {
		delete e.config.globalProperties.$i18n, gh.forEach((t) => {
			delete e.config.globalProperties[`$${t}`];
		});
	};
}
b({
	value: {
		type: [Number, Date],
		required: !0
	},
	format: { type: [String, Object] }
}, fh), Em(), Gt(vt), qt(Rt), Yt(Tt);
//#endregion
//#region src/i18n/locales/en.json
var vh = {
	"android.browser.errors.applicationUnavailable": "No application can open this link.",
	"android.browser.errors.browserUnavailable": "No browser is available to open this link.",
	"android.browser.errors.certificate": "The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.",
	"android.browser.errors.engineStart": "{engine} could not start. Check the engine installation, then reopen TwiCC.",
	"android.browser.errors.httpStatus": "The server returned HTTP {status}. Check your tunnel or server, then retry.",
	"android.browser.errors.instanceRemoved": "This instance is no longer saved.",
	"android.browser.errors.linkUnsupported": "This link type is not supported.",
	"android.browser.errors.pageBlocked": "This page cannot open inside TwiCC. Retry to return to your server.",
	"android.browser.errors.rendererStopped": "The page stopped unexpectedly. Retry to reopen TwiCC.",
	"android.browser.errors.sessionClosed": "The browser session is closed.",
	"android.browser.errors.unreachable": "Cannot reach {name}. Check your connection and HTTPS tunnel, then retry.",
	"android.browser.errors.webviewProfiles": "Update Android System WebView or Chrome to use separate instances.",
	"android.browser.menuDescription": "Android app menu for {name}",
	"android.browser.version": "TwiCC {version} · {engine}",
	"android.downloads.browserRequired": "This download needs a browser. Open the page in your browser to save blob or data exports.",
	"android.downloads.errors.failed": "Download failed. Check your connection and try again.",
	"android.downloads.errors.saveUnavailable": "This download cannot be saved. Try opening the page in your browser.",
	"android.downloads.errors.startFailed": "The download could not start. Try opening this page in your browser.",
	"android.downloads.saved": "Saved {filename} to Downloads.",
	"android.downloads.saving": "Saving {filename} to Downloads…",
	"android.downloads.started": "Download started. Check Android's Downloads notification.",
	"android.files.errors.folderUnsupported": "Folder uploads are not available. Select individual files instead.",
	"android.files.errors.pickerUnavailable": "No file picker is available.",
	"android.files.errors.readFailed": "The selected files could not be read. Try again.",
	"android.instances.add": "Add instance",
	"android.instances.addressHint": "HTTPS only. A new address starts with a fresh login.",
	"android.instances.addressLabel": "Server address",
	"android.instances.editDescription": "Edit {name}",
	"android.instances.editTitle": "Edit instance",
	"android.instances.emptyBody": "Add the address of your TwiCC server to get started.",
	"android.instances.emptyTitle": "No instances yet",
	"android.instances.errors.duplicate": "This server address is already saved.",
	"android.instances.errors.httpsRequired": "Use an HTTPS address, such as https://twicc.example.com.",
	"android.instances.errors.invalidAddress": "Enter a valid HTTPS address.",
	"android.instances.errors.invalidPort": "Enter a valid port number.",
	"android.instances.errors.originRequired": "Use the server address without credentials, a query, or a fragment.",
	"android.instances.errors.rootRequired": "Use the server root address, without a page path.",
	"android.instances.lastUsed": "Last used",
	"android.instances.nameLabel": "Name (optional)",
	"android.instances.openDescription": "Open {name}, {address}",
	"android.instances.remove": "Remove",
	"android.instances.removeDescription": "Remove {name}",
	"android.instances.removeMessage": "Remove this address and request deletion of its login and Web storage in each engine. The server is unchanged.",
	"android.instances.removeTitle": "Remove {name}?",
	"android.instances.saved": "Saved instances",
	"android.instances.subtitle": "Connect to your existing TwiCC server through an HTTPS tunnel.",
	"android.instances.title": "TwiCC",
	"android.language.errors.changed": "The app language changed. Select the language again.",
	"android.language.errors.invalidRevision": "The app language request is invalid. Reload this page and try again.",
	"android.language.errors.revisionExhausted": "The app language cannot change.",
	"android.language.errors.saveFailed": "Could not save the app language. Try again.",
	"android.language.errors.staleDocument": "Reload this page before changing the app language.",
	"android.language.errors.unsupported": "This language is unavailable in the Android app. Update the app and try again.",
	"android.language.errors.updateApp": "Update the Android app to synchronize the interface language.",
	"android.language.errors.updateServer": "Update this TwiCC server to synchronize the interface language.",
	"android.push.channels.attention": "Questions and approvals",
	"android.push.channels.finished": "Finished work",
	"android.push.channels.peer": "Peer messages and requests",
	"android.push.distributorLabel": "TwiCC notifications",
	"android.push.errors.updateApp": "Android notifications are unavailable. Update the Android app and try again.",
	"android.push.errors.updateServer": "Update this TwiCC server to use detailed Android notifications. Until then, notifications show a generic message.",
	"android.push.extraUsageBody": "{sentence}\n{usage}",
	"android.push.finishedWithShells": "{finished} — {shells}",
	"android.push.genericBody": "Open TwiCC to view this notification.",
	"android.push.genericTitle": "TwiCC notification",
	"android.push.peerBody": "\"{title}\"\n{preview}",
	"android.push.peerBodyWithProject": "{message}\nProject: {project}",
	"android.push.peerBodyWithSession": "{message}\nSession: {session}",
	"android.push.peerRequestBody": "{peer} wants to pair with your instance.\nAddress: {address}",
	"android.push.projectWithParent": "{parent} › {project}",
	"android.push.soundTitle": "TwiCC — {sound}",
	"android.push.titleWithInstance": "{title} · {instance}",
	"android.speech.batteryAdvice": "You can use timeline listening without changing battery settings. Android may delay or interrupt speech in the background or with the screen off. Allow unrestricted battery use for TwiCC to improve background listening. You can change this later in Android Settings → Apps → TwiCC → Battery.\n\nContinue without changes to start now, or open Settings and then enable listening again.",
	"android.speech.batteryTitle": "Timeline listening and battery",
	"android.speech.channel": "Timeline speech",
	"android.speech.continueWithoutChanges": "Continue without changes",
	"android.speech.errors.androidStopped": "Android stopped speech. Start it again.",
	"android.speech.errors.audioEmpty": "TTS audio is empty",
	"android.speech.errors.audioFormat": "Unsupported speech audio format",
	"android.speech.errors.audioRequest": "TTS request failed ({status})",
	"android.speech.errors.batteryChoiceRequired": "Choose how to continue in the battery advice dialog.",
	"android.speech.errors.changeFailed": "Could not change speech playback. Reload the page and try again.",
	"android.speech.errors.cookieSupport": "Native speech requires a WebView with GET_COOKIE_INFO support. Update Android System WebView.",
	"android.speech.errors.credentialsUnavailable": "Reopen this instance, then start speech again.",
	"android.speech.errors.failed": "Speech failed",
	"android.speech.errors.generationFailed": "Speech generation failed",
	"android.speech.errors.instanceChanged": "The active instance changed.",
	"android.speech.errors.instanceClosed": "The active instance was closed. Start speech again to continue.",
	"android.speech.errors.instanceRequired": "Open a saved instance before starting speech.",
	"android.speech.errors.interrupted": "Audio playback was interrupted.",
	"android.speech.errors.playbackChanged": "Speech playback changed. Try again.",
	"android.speech.errors.playbackFailed": "Audio playback could not start.",
	"android.speech.errors.retryPlayback": "Could not reach TwiCC. Start playback again.",
	"android.speech.errors.staleDocument": "Reload this page, then try speech again.",
	"android.speech.errors.staleSession": "This notification belongs to an old speech session.",
	"android.speech.errors.stopping": "Speech is stopping. Wait a moment, then try again.",
	"android.speech.errors.updateRequired": "Speech is unavailable. Update the Android app and try again.",
	"android.speech.errors.visibleRequired": "Open TwiCC before starting speech.",
	"android.speech.notificationTitle": "{name} · Speech",
	"android.speech.notificationsRequired": "Enable speech notifications to use background Skip and Stop all controls. Then start speech again.",
	"android.speech.openSettings": "Open Settings",
	"android.speech.pauseTimeline": "Pause",
	"android.speech.preparing": "Preparing",
	"android.speech.reading": "Reading",
	"android.speech.reconnecting": "Reconnecting",
	"android.speech.recoveryAnnouncement": "Connection restored. Messages received during the interruption will not be read. Automatic reading resumes.",
	"android.speech.resumeTimeline": "Resume",
	"android.speech.settingsTitle": "Speech settings required",
	"android.speech.skip": "Skip",
	"android.speech.stopAll": "Stop",
	"android.speech.stopping": "Stopping",
	"android.speech.timelinePaused": "Timeline reading paused",
	"android.speech.waiting": "Waiting",
	"backend.tts.adaptationStopped": "The text adaptation stopped before the end",
	"backend.tts.apiKeyRequired": "TWICC_OPENROUTER_API_KEY is not set. Add it to {path} and restart TwiCC.",
	"backend.tts.audioCut": "The audio stopped before the end",
	"backend.tts.audioExpired": "The audio expired before playback",
	"backend.tts.audioGone": "The audio is no longer available",
	"backend.tts.authenticationExpired": "Authentication expired. Open TwiCC and sign in again.",
	"backend.tts.catchupIncomplete": "Some messages could not be loaded. Turn timeline reading on again.",
	"backend.tts.chooseMp3": "The script output looks like MP3: choose MP3",
	"backend.tts.chooseWav": "The script output looks like WAV: choose WAV",
	"backend.tts.commandMissing": "This script config has no command",
	"backend.tts.configMatches": "'config_id' must equal 'script_id'",
	"backend.tts.configNotSaved": "This config is not saved yet: try again",
	"backend.tts.configNotValidated": "This script config is not validated: test it again in the settings",
	"backend.tts.configurationUnavailable": "Text-to-speech is unavailable. Check the active configuration.",
	"backend.tts.failed": "Text-to-speech failed",
	"backend.tts.fieldString": "'{field}' must be a string",
	"backend.tts.flacUnsupported": "The script output looks like FLAC audio, which TwiCC does not play",
	"backend.tts.formatChoices": "'response_format' must be one of: {choices}",
	"backend.tts.invalidCommand": "Speech could not start. Reload the page and try again.",
	"backend.tts.modelRequired": "'model' must be a non-empty string",
	"backend.tts.mp4Unsupported": "The script output looks like MP4 audio, which TwiCC does not play",
	"backend.tts.noAudio": "The script gave no audio",
	"backend.tts.notMp3": "The script output is not MP3",
	"backend.tts.notWav": "The script output is not WAV",
	"backend.tts.nothingToRead": "There is nothing to read aloud in this text",
	"backend.tts.oggUnsupported": "The script output looks like Ogg audio, which TwiCC does not play",
	"backend.tts.openrouterStatus": "OpenRouter returned HTTP {status}",
	"backend.tts.openrouterTimeout": "OpenRouter did not answer in time",
	"backend.tts.openrouterUnreachable": "Could not reach OpenRouter",
	"backend.tts.originChoices": "'origin' must be one of: {choices}",
	"backend.tts.partFormatChanged": "The audio format changed between two parts of the text",
	"backend.tts.pcmRateRequired": "This script config needs a sample rate for PCM",
	"backend.tts.provider400Hint": "Provider returned 400. Check the model, the voice, the format and the text length.",
	"backend.tts.scopeUnavailable": "Timeline reading stopped. Choose another timeline.",
	"backend.tts.scriptAudioLarge": "The script audio is too large",
	"backend.tts.scriptExit": "The script failed (exit status {status})",
	"backend.tts.scriptId": "'script_id' must be a non-empty string",
	"backend.tts.scriptMissing": "This script config does not exist",
	"backend.tts.scriptOutputPath": "The script output is not one absolute path",
	"backend.tts.scriptPathReused": "The script returned the same path twice",
	"backend.tts.scriptSignal": "The script was stopped by signal {signal}",
	"backend.tts.scriptStalled": "The script stopped sending audio",
	"backend.tts.scriptStart": "The script could not start",
	"backend.tts.scriptUnreadable": "The script file cannot be read",
	"backend.tts.sessionLost": "Speech stopped. Start playback or turn on timeline reading again.",
	"backend.tts.speedNumber": "'speed' must be a finite positive number",
	"backend.tts.stopped": "Text-to-speech stopped",
	"backend.tts.summaryChanged": "The saved summary changed. Refresh it before reading.",
	"backend.tts.summaryFingerprint": "'summary_result_hash' must be the saved result fingerprint",
	"backend.tts.summaryGone": "The summary is no longer available",
	"backend.tts.summaryHandle": "'summary_id' must be a non-empty handle",
	"backend.tts.summaryHashRequiresSaved": "'summary_result_hash' requires 'saved_summary_id'",
	"backend.tts.summaryReference": "A summary needs exactly one reference, without text or a configuration test",
	"backend.tts.summarySavedGone": "The saved summary is no longer available",
	"backend.tts.summarySavedId": "'saved_summary_id' must be a positive integer",
	"backend.tts.summaryStopped": "The summary stopped before the end",
	"backend.tts.textRequired": "'text' must be a non-empty string",
	"backend.tts.textTooLong": "The text is too long to send",
	"backend.tts.wav16bit": "The script WAV is not 16-bit PCM",
	"backend.tts.wavFormat": "The script WAV has an unsupported sample rate or channel count",
	"common.actions.cancel": "Cancel",
	"common.actions.ok": "OK",
	"common.actions.retry": "Retry",
	"common.actions.save": "Save",
	"common.android.errors.actionFailed": "Could not complete this action. Reload this page and try again.",
	"common.android.errors.closed": "The connection to the Android app closed. Reload this page and try again.",
	"common.android.errors.invalidNotification": "Invalid notification target.",
	"common.android.errors.speechPreparing": "Speech is getting ready. Try again in a moment.",
	"common.android.errors.tooLarge": "This action contains too much data. Try a smaller selection.",
	"common.android.errors.unreachable": "Could not contact the Android app. Reload this page and try again.",
	"common.labels.unknown": "Unknown",
	"common.sessionCount": "{count} session | {count} sessions",
	"composer.status.startingProvider": "Starting {provider} process...",
	"navigation.android.active": "Active",
	"navigation.android.browser": "Open in browser",
	"navigation.android.forceRefresh": "Reload page (bypass cache)",
	"navigation.android.manage": "Manage instances",
	"navigation.android.menu": "Android app menu",
	"navigation.android.refresh": "Refresh",
	"navigation.android.refreshHint": "Reload this page and bypass its resource cache. Your login and preferences stay saved. Unsaved text may be lost.",
	"navigation.android.refreshQuestion": "Force refresh?",
	"navigation.android.reload": "Reload page",
	"navigation.android.serverHome": "Server home",
	"navigation.android.unsupportedState": "Unsupported Android app state.",
	"peer.toast.unnamedInstance": "An instance",
	"session.notifications.activityTimeout": "{provider} stopped: no activity for 3 hours",
	"session.notifications.agentStoppedBeforeAck": "The agent stopped before confirming your message was received.",
	"session.notifications.approval": "🖐️ {provider} needs your approval",
	"session.notifications.attention": "{provider} needs your attention",
	"session.notifications.body": "Project: {project}\nSession: {session}",
	"session.notifications.creditsRemaining": "{count} credit remaining. | {count} credits remaining.",
	"session.notifications.creditsUsed": "{used} credit used. | {used} credits used.",
	"session.notifications.creditsUsedOf": "{used} of {limit} credit used. | {used} of {limit} credits used.",
	"session.notifications.enabledLabel": "Notifications on",
	"session.notifications.ephemeral": "Ephemeral session finished",
	"session.notifications.ephemeralSession": "Ephemeral session",
	"session.notifications.errors.update": "Failed to update session notifications",
	"session.notifications.extraUsageSentence": "{provider} is currently drawing from your extra usage credit, billed on top of your plan.",
	"session.notifications.extraUsageTitle": "{provider} — Extra usage started",
	"session.notifications.finished": "{provider} finished working",
	"session.notifications.instance": "an instance",
	"session.notifications.invalidTitle": "Invalid title",
	"session.notifications.invalidTitleDetail": "Invalid session title: {error}",
	"session.notifications.invalidTitleUnknown": "Invalid session title: Unknown error",
	"session.notifications.moneyUsed": "{used} {currency} used.",
	"session.notifications.moneyUsedOf": "{used} of {limit} {currency} used.",
	"session.notifications.mute": "Mute",
	"session.notifications.muteTooltip": "Notifications on — click to silence the \"finished working\" notification and stop this session showing as unread",
	"session.notifications.mutedLabel": "Muted",
	"session.notifications.notConnected": "Not connected",
	"session.notifications.peerAccepted": "\"{peer}\" accepted your peer request",
	"session.notifications.peerDelivered": "Your message{title} to \"{peer}\" was delivered",
	"session.notifications.peerDone": "\"{peer}\" marked your message{title} as done",
	"session.notifications.peerMessage": "Message from {peer}",
	"session.notifications.peerRefused": "Your message{title} to \"{peer}\" was refused",
	"session.notifications.peerReply": "Reply from {peer}",
	"session.notifications.peerRequest": "Peer request",
	"session.notifications.peerRequestFrom": "Peer request from {peer}",
	"session.notifications.peerVerification": "\"{peer}\" accepted your request — complete the code verification to activate",
	"session.notifications.providerDisabled": "Cannot send to {provider}: it is disabled. Enable it from Settings → Providers.",
	"session.notifications.question": "🖐️ {provider} has a question for you",
	"session.notifications.settingsInvalid": "A settings value is invalid.",
	"session.notifications.shells": "{count} background shell still running | {count} background shells still running",
	"session.notifications.startTimeout": "{provider} stopped: failed to start within 1 minute",
	"session.notifications.terminated": "{provider} terminated due to error",
	"session.notifications.turnFinished": "{provider} finished its turn",
	"session.notifications.unknownError": "Unknown error",
	"session.notifications.unmuteTooltip": "Muted — click to restore the \"finished working\" notification and the unread flag",
	"session.notifications.updateAvailable": "TwiCC v{version} is available",
	"session.notifications.viewChanges": "View changes",
	"session.speech.errors.cut": "The audio stopped before the end",
	"session.speech.errors.gone": "The audio is no longer available",
	"session.speech.errors.identityRequired": "A summary identity is required",
	"session.speech.errors.initializing": "Native speech is initializing. Try again.",
	"session.speech.errors.nativeUnavailable": "Native speech is unavailable. Reopen this page.",
	"session.speech.errors.noConfig": "No active text-to-speech config",
	"session.speech.errors.refused": "The browser refused to play the audio",
	"session.speech.errors.requestFailed": "Request failed ({status})",
	"session.speech.errors.startFailed": "Speech could not start. Reload the page and try again.",
	"session.speech.errors.summaryGone": "The summary is no longer available",
	"session.speech.errors.unavailable": "Speech is unavailable. Reload the page and try again.",
	"session.speech.errors.unreachable": "Could not reach TwiCC",
	"settings.general.language.label": "Language",
	"settings.notifications.sounds.beep": "Beep",
	"settings.notifications.sounds.bloop": "Bloop",
	"settings.notifications.sounds.chime": "Chime",
	"settings.notifications.sounds.knock": "Knock",
	"settings.notifications.sounds.ping": "Ping",
	"settings.notifications.sounds.pop": "Pop",
	"settings.notifications.sounds.ripple": "Ripple",
	"settings.notifications.sounds.signal": "Signal",
	"settings.notifications.sounds.sweep": "Sweep",
	"settings.notifications.sounds.tink": "Tink",
	"settings.notifications.targets.extraUsage": "Extra usage",
	"workspace.projectCount": "{count} project | {count} projects"
}, yh, bh = o((() => {
	yh = {
		"android.browser.errors.applicationUnavailable": "Keine Anwendung kann diesen Link öffnen.",
		"android.browser.errors.browserUnavailable": "Kein Browser ist verfügbar, um diesen Link zu öffnen.",
		"android.browser.errors.certificate": "Das HTTPS-Zertifikat konnte nicht geprüft werden. Prüfe das Tunnelzertifikat und das Datum deines Telefons.",
		"android.browser.errors.engineStart": "{engine} konnte nicht starten. Prüfe die Installation der Engine und öffne TwiCC erneut.",
		"android.browser.errors.httpStatus": "Der Server hat HTTP {status} zurückgegeben. Prüfe deinen Tunnel oder Server und versuche es erneut.",
		"android.browser.errors.instanceRemoved": "Diese Instanz ist nicht mehr gespeichert.",
		"android.browser.errors.linkUnsupported": "Dieser Linktyp wird nicht unterstützt.",
		"android.browser.errors.pageBlocked": "Diese Seite kann nicht in TwiCC geöffnet werden. Versuche es erneut, um zu deinem Server zurückzukehren.",
		"android.browser.errors.rendererStopped": "Die Seite wurde unerwartet gestoppt. Versuche erneut, TwiCC zu öffnen.",
		"android.browser.errors.sessionClosed": "Die Browser-Sitzung ist geschlossen.",
		"android.browser.errors.unreachable": "{name} ist nicht erreichbar. Prüfe deine Verbindung und den HTTPS-Tunnel und versuche es erneut.",
		"android.browser.errors.webviewProfiles": "Aktualisiere Android System WebView oder Chrome, um getrennte Instanzen zu verwenden.",
		"android.browser.menuDescription": "Android-App-Menü für {name}",
		"android.browser.version": "TwiCC {version} · {engine}",
		"android.downloads.browserRequired": "Dieser Download benötigt einen Browser. Öffne die Seite in deinem Browser, um Blob- oder Datenexporte zu speichern.",
		"android.downloads.errors.failed": "Download fehlgeschlagen. Prüfe deine Verbindung und versuche es erneut.",
		"android.downloads.errors.saveUnavailable": "Dieser Download kann nicht gespeichert werden. Öffne die Seite in deinem Browser.",
		"android.downloads.errors.startFailed": "Der Download konnte nicht starten. Öffne diese Seite in deinem Browser.",
		"android.downloads.saved": "{filename} wurde in Downloads gespeichert.",
		"android.downloads.saving": "{filename} wird in Downloads gespeichert…",
		"android.downloads.started": "Download gestartet. Prüfe die Download-Benachrichtigung von Android.",
		"android.files.errors.folderUnsupported": "Ordner-Uploads sind nicht verfügbar. Wähle stattdessen einzelne Dateien aus.",
		"android.files.errors.pickerUnavailable": "Keine Dateiauswahl ist verfügbar.",
		"android.files.errors.readFailed": "Die ausgewählten Dateien konnten nicht gelesen werden. Versuche es erneut.",
		"android.instances.add": "Instanz hinzufügen",
		"android.instances.addressHint": "Nur HTTPS. Eine neue Adresse beginnt mit einer neuen Anmeldung.",
		"android.instances.addressLabel": "Serveradresse",
		"android.instances.editDescription": "{name} bearbeiten",
		"android.instances.editTitle": "Instanz bearbeiten",
		"android.instances.emptyBody": "Füge die Adresse deines TwiCC-Servers hinzu, um zu starten.",
		"android.instances.emptyTitle": "Noch keine Instanzen",
		"android.instances.errors.duplicate": "Diese Serveradresse ist bereits gespeichert.",
		"android.instances.errors.httpsRequired": "Verwende eine HTTPS-Adresse, etwa https://twicc.example.com.",
		"android.instances.errors.invalidAddress": "Gib eine gültige HTTPS-Adresse ein.",
		"android.instances.errors.invalidPort": "Gib eine gültige Portnummer ein.",
		"android.instances.errors.originRequired": "Verwende die Serveradresse ohne Zugangsdaten, Abfrage oder Fragment.",
		"android.instances.errors.rootRequired": "Verwende die Stammadresse des Servers ohne Seitenpfad.",
		"android.instances.lastUsed": "Zuletzt verwendet",
		"android.instances.nameLabel": "Name (optional)",
		"android.instances.openDescription": "{name}, {address} öffnen",
		"android.instances.remove": "Entfernen",
		"android.instances.removeDescription": "{name} entfernen",
		"android.instances.removeMessage": "Entferne diese Adresse und fordere die Löschung ihrer Anmeldung und ihres Webspeichers in jeder Engine an. Der Server bleibt unverändert.",
		"android.instances.removeTitle": "{name} entfernen?",
		"android.instances.saved": "Gespeicherte Instanzen",
		"android.instances.subtitle": "Verbinde dich über einen HTTPS-Tunnel mit deinem vorhandenen TwiCC-Server.",
		"android.instances.title": "TwiCC",
		"android.language.errors.changed": "Die App-Sprache hat sich geändert. Wähle die Sprache erneut aus.",
		"android.language.errors.invalidRevision": "Die Anfrage zur App-Sprache ist ungültig. Lade diese Seite neu und versuche es erneut.",
		"android.language.errors.revisionExhausted": "Die App-Sprache kann nicht geändert werden.",
		"android.language.errors.saveFailed": "Die App-Sprache konnte nicht gespeichert werden. Versuche es erneut.",
		"android.language.errors.staleDocument": "Lade diese Seite neu, bevor du die App-Sprache änderst.",
		"android.language.errors.unsupported": "Diese Sprache ist in der Android-App nicht verfügbar. Aktualisiere die App und versuche es erneut.",
		"android.language.errors.updateApp": "Aktualisiere die Android-App, um die Oberflächensprache zu synchronisieren.",
		"android.language.errors.updateServer": "Aktualisiere diesen TwiCC-Server, um die Oberflächensprache zu synchronisieren.",
		"android.push.channels.attention": "Fragen und Genehmigungen",
		"android.push.channels.finished": "Abgeschlossene Arbeit",
		"android.push.channels.peer": "Peer-Nachrichten und -Anfragen",
		"android.push.distributorLabel": "TwiCC-Benachrichtigungen",
		"android.push.errors.updateApp": "Android-Benachrichtigungen sind nicht verfügbar. Aktualisiere die Android-App und versuche es erneut.",
		"android.push.errors.updateServer": "Aktualisiere diesen TwiCC-Server, um detaillierte Android-Benachrichtigungen zu verwenden. Bis dahin zeigen Benachrichtigungen eine allgemeine Nachricht.",
		"android.push.extraUsageBody": "{sentence}\n{usage}",
		"android.push.finishedWithShells": "{finished} — {shells}",
		"android.push.genericBody": "Öffne TwiCC, um diese Benachrichtigung anzusehen.",
		"android.push.genericTitle": "TwiCC-Benachrichtigung",
		"android.push.peerBody": "„{title}“\n{preview}",
		"android.push.peerBodyWithProject": "{message}\nProjekt: {project}",
		"android.push.peerBodyWithSession": "{message}\nSitzung: {session}",
		"android.push.peerRequestBody": "{peer} möchte sich mit deiner Instanz koppeln.\nAdresse: {address}",
		"android.push.projectWithParent": "{parent} › {project}",
		"android.push.soundTitle": "TwiCC — {sound}",
		"android.push.titleWithInstance": "{title} · {instance}",
		"android.speech.batteryAdvice": "Du kannst die Zeitachse anhören, ohne die Akkueinstellungen zu ändern. Android kann die Sprachausgabe im Hintergrund oder bei ausgeschaltetem Bildschirm verzögern oder unterbrechen. Erlaube TwiCC uneingeschränkte Akkunutzung, um das Anhören im Hintergrund zu verbessern. Du kannst dies später unter Android-Einstellungen → Apps → TwiCC → Akku ändern.\n\nFahre ohne Änderungen fort, um jetzt zu starten, oder öffne die Einstellungen und aktiviere danach das Anhören erneut.",
		"android.speech.batteryTitle": "Zeitachse anhören und Akku",
		"android.speech.channel": "Sprachausgabe der Zeitachse",
		"android.speech.continueWithoutChanges": "Ohne Änderungen fortfahren",
		"android.speech.errors.androidStopped": "Android hat die Sprachausgabe gestoppt. Starte sie erneut.",
		"android.speech.errors.audioEmpty": "TTS-Audio ist leer",
		"android.speech.errors.audioFormat": "Nicht unterstütztes Audioformat für Sprachausgabe",
		"android.speech.errors.audioRequest": "TTS-Anfrage fehlgeschlagen ({status})",
		"android.speech.errors.batteryChoiceRequired": "Wähle im Akkudialog aus, wie du fortfahren möchtest.",
		"android.speech.errors.changeFailed": "Die Sprachausgabe konnte nicht geändert werden. Lade die Seite neu und versuche es erneut.",
		"android.speech.errors.cookieSupport": "Native Sprachausgabe benötigt eine WebView mit GET_COOKIE_INFO-Unterstützung. Aktualisiere Android System WebView.",
		"android.speech.errors.credentialsUnavailable": "Öffne diese Instanz erneut und starte die Sprachausgabe erneut.",
		"android.speech.errors.failed": "Sprachausgabe fehlgeschlagen",
		"android.speech.errors.generationFailed": "Spracherzeugung fehlgeschlagen",
		"android.speech.errors.instanceChanged": "Die aktive Instanz hat sich geändert.",
		"android.speech.errors.instanceClosed": "Die aktive Instanz wurde geschlossen. Starte die Sprachausgabe erneut, um fortzufahren.",
		"android.speech.errors.instanceRequired": "Öffne eine gespeicherte Instanz, bevor du die Sprachausgabe startest.",
		"android.speech.errors.interrupted": "Die Audiowiedergabe wurde unterbrochen.",
		"android.speech.errors.playbackChanged": "Die Sprachausgabe hat sich geändert. Versuche es erneut.",
		"android.speech.errors.playbackFailed": "Die Audiowiedergabe konnte nicht starten.",
		"android.speech.errors.retryPlayback": "TwiCC ist nicht erreichbar. Starte die Wiedergabe erneut.",
		"android.speech.errors.staleDocument": "Lade diese Seite neu und versuche die Sprachausgabe erneut.",
		"android.speech.errors.staleSession": "Diese Benachrichtigung gehört zu einer alten Sprachausgabe-Sitzung.",
		"android.speech.errors.stopping": "Die Sprachausgabe wird gestoppt. Warte einen Moment und versuche es erneut.",
		"android.speech.errors.updateRequired": "Sprachausgabe ist nicht verfügbar. Aktualisiere die Android-App und versuche es erneut.",
		"android.speech.errors.visibleRequired": "Öffne TwiCC, bevor du die Sprachausgabe startest.",
		"android.speech.notificationTitle": "{name} · Sprachausgabe",
		"android.speech.notificationsRequired": "Aktiviere Sprachausgabe-Benachrichtigungen, um im Hintergrund Überspringen und Alle stoppen zu verwenden. Starte danach die Sprachausgabe erneut.",
		"android.speech.openSettings": "Einstellungen öffnen",
		"android.speech.pauseTimeline": "Pause",
		"android.speech.preparing": "Vorbereitung",
		"android.speech.reading": "Wird vorgelesen",
		"android.speech.reconnecting": "Verbindung wird wiederhergestellt",
		"android.speech.recoveryAnnouncement": "Verbindung wiederhergestellt. Nachrichten aus der Unterbrechung werden nicht vorgelesen. Das automatische Vorlesen wird fortgesetzt.",
		"android.speech.resumeTimeline": "Fortsetzen",
		"android.speech.settingsTitle": "Einstellungen für Sprachausgabe erforderlich",
		"android.speech.skip": "Überspringen",
		"android.speech.stopAll": "Stoppen",
		"android.speech.stopping": "Wird gestoppt",
		"android.speech.timelinePaused": "Vorlesen der Zeitachse pausiert",
		"android.speech.waiting": "Warten",
		"backend.tts.adaptationStopped": "Die Anpassung des Textes wurde vor dem Ende gestoppt",
		"backend.tts.apiKeyRequired": "TWICC_OPENROUTER_API_KEY ist nicht gesetzt. Füge sie in {path} hinzu und starte TwiCC neu.",
		"backend.tts.audioCut": "Das Audio wurde vor dem Ende gestoppt",
		"backend.tts.audioExpired": "Das Audio ist vor der Wiedergabe abgelaufen",
		"backend.tts.audioGone": "Das Audio ist nicht mehr verfügbar",
		"backend.tts.authenticationExpired": "Die Authentifizierung ist abgelaufen. Öffne TwiCC und melde dich erneut an.",
		"backend.tts.catchupIncomplete": "Einige Nachrichten konnten nicht geladen werden. Aktiviere das Vorlesen der Zeitachse erneut.",
		"backend.tts.chooseMp3": "Die Ausgabe des Skripts sieht wie MP3 aus: Wähle MP3",
		"backend.tts.chooseWav": "Die Ausgabe des Skripts sieht wie WAV aus: Wähle WAV",
		"backend.tts.commandMissing": "Diese Skriptkonfiguration hat keinen Befehl",
		"backend.tts.configMatches": "'config_id' muss gleich 'script_id' sein",
		"backend.tts.configNotSaved": "Diese Konfiguration ist noch nicht gespeichert: Versuche es erneut",
		"backend.tts.configNotValidated": "Diese Skriptkonfiguration ist nicht validiert: Teste sie in den Einstellungen erneut",
		"backend.tts.configurationUnavailable": "Die Sprachausgabe ist nicht verfügbar. Prüfe die aktive Konfiguration.",
		"backend.tts.failed": "Sprachausgabe fehlgeschlagen",
		"backend.tts.fieldString": "'{field}' muss ein String sein",
		"backend.tts.flacUnsupported": "Die Ausgabe des Skripts sieht wie FLAC-Audio aus, das TwiCC nicht abspielt",
		"backend.tts.formatChoices": "'response_format' muss einer der folgenden Werte sein: {choices}",
		"backend.tts.invalidCommand": "Die Sprachausgabe konnte nicht gestartet werden. Lade die Seite neu und versuche es erneut.",
		"backend.tts.modelRequired": "'model' muss ein nicht leerer String sein",
		"backend.tts.mp4Unsupported": "Die Ausgabe des Skripts sieht wie MP4-Audio aus, das TwiCC nicht abspielt",
		"backend.tts.noAudio": "Das Skript hat kein Audio geliefert",
		"backend.tts.notMp3": "Die Ausgabe des Skripts ist kein MP3",
		"backend.tts.notWav": "Die Ausgabe des Skripts ist kein WAV",
		"backend.tts.nothingToRead": "In diesem Text gibt es nichts vorzulesen",
		"backend.tts.oggUnsupported": "Die Ausgabe des Skripts sieht wie Ogg-Audio aus, das TwiCC nicht abspielt",
		"backend.tts.openrouterStatus": "OpenRouter hat HTTP {status} zurückgegeben",
		"backend.tts.openrouterTimeout": "OpenRouter hat nicht rechtzeitig geantwortet",
		"backend.tts.openrouterUnreachable": "OpenRouter war nicht erreichbar",
		"backend.tts.originChoices": "'origin' muss einer der folgenden Werte sein: {choices}",
		"backend.tts.partFormatChanged": "Das Audioformat hat sich zwischen zwei Teilen des Textes geändert",
		"backend.tts.pcmRateRequired": "Diese Skriptkonfiguration benötigt für PCM eine Abtastrate",
		"backend.tts.provider400Hint": "Der Anbieter hat 400 zurückgegeben. Prüfe Modell, Stimme, Format und Textlänge.",
		"backend.tts.scopeUnavailable": "Das Vorlesen der Zeitachse wurde gestoppt. Wähle eine andere Zeitachse.",
		"backend.tts.scriptAudioLarge": "Das Audio des Skripts ist zu groß",
		"backend.tts.scriptExit": "Das Skript ist fehlgeschlagen (Exit-Status {status})",
		"backend.tts.scriptId": "'script_id' muss ein nicht leerer String sein",
		"backend.tts.scriptMissing": "Diese Skriptkonfiguration existiert nicht",
		"backend.tts.scriptOutputPath": "Die Ausgabe des Skripts ist kein einzelner absoluter Pfad",
		"backend.tts.scriptPathReused": "Das Skript hat zweimal denselben Pfad zurückgegeben",
		"backend.tts.scriptSignal": "Das Skript wurde durch das Signal {signal} gestoppt",
		"backend.tts.scriptStalled": "Das Skript hat kein Audio mehr gesendet",
		"backend.tts.scriptStart": "Das Skript konnte nicht gestartet werden",
		"backend.tts.scriptUnreadable": "Die Skriptdatei kann nicht gelesen werden",
		"backend.tts.sessionLost": "Die Sprachausgabe wurde gestoppt. Starte die Wiedergabe oder aktiviere das Vorlesen der Zeitachse erneut.",
		"backend.tts.speedNumber": "'speed' muss eine endliche positive Zahl sein",
		"backend.tts.stopped": "Sprachausgabe gestoppt",
		"backend.tts.summaryChanged": "Die gespeicherte Zusammenfassung hat sich geändert. Aktualisiere sie vor dem Vorlesen.",
		"backend.tts.summaryFingerprint": "'summary_result_hash' muss der Fingerabdruck des gespeicherten Ergebnisses sein",
		"backend.tts.summaryGone": "Die Zusammenfassung ist nicht mehr verfügbar",
		"backend.tts.summaryHandle": "'summary_id' muss ein nicht leerer Handle sein",
		"backend.tts.summaryHashRequiresSaved": "'summary_result_hash' erfordert 'saved_summary_id'",
		"backend.tts.summaryReference": "Eine Zusammenfassung benötigt genau eine Referenz, ohne Text und ohne Konfigurationstest",
		"backend.tts.summarySavedGone": "Die gespeicherte Zusammenfassung ist nicht mehr verfügbar",
		"backend.tts.summarySavedId": "'saved_summary_id' muss eine positive Ganzzahl sein",
		"backend.tts.summaryStopped": "Die Zusammenfassung wurde vor dem Ende gestoppt",
		"backend.tts.textRequired": "'text' muss ein nicht leerer String sein",
		"backend.tts.textTooLong": "Der Text ist zu lang zum Senden",
		"backend.tts.wav16bit": "Das WAV des Skripts ist kein 16-Bit-PCM",
		"backend.tts.wavFormat": "Das WAV des Skripts hat eine nicht unterstützte Abtastrate oder Kanalanzahl",
		"common.actions.cancel": "Abbrechen",
		"common.actions.ok": "OK",
		"common.actions.retry": "Erneut versuchen",
		"common.actions.save": "Speichern",
		"common.android.errors.actionFailed": "Diese Aktion konnte nicht abgeschlossen werden. Lade die Seite neu und versuche es erneut.",
		"common.android.errors.closed": "Die Verbindung zur Android-App wurde geschlossen. Lade die Seite neu und versuche es erneut.",
		"common.android.errors.invalidNotification": "Ungültiges Benachrichtigungsziel.",
		"common.android.errors.speechPreparing": "Die Sprachausgabe wird vorbereitet. Versuche es gleich noch einmal.",
		"common.android.errors.tooLarge": "Diese Aktion enthält zu viele Daten. Wähle weniger aus.",
		"common.android.errors.unreachable": "Die Android-App konnte nicht erreicht werden. Lade die Seite neu und versuche es erneut.",
		"common.labels.unknown": "Unbekannt",
		"common.sessionCount": "{count} Sitzung | {count} Sitzungen",
		"composer.status.startingProvider": "{provider}-Prozess wird gestartet...",
		"navigation.android.active": "Aktiv",
		"navigation.android.browser": "Im Browser öffnen",
		"navigation.android.forceRefresh": "Seite neu laden (ohne Cache)",
		"navigation.android.manage": "Instanzen verwalten",
		"navigation.android.menu": "Menü der Android-App",
		"navigation.android.refresh": "Aktualisieren",
		"navigation.android.refreshHint": "Lädt diese Seite neu und umgeht ihren Ressourcen-Cache. Deine Anmeldung und deine Einstellungen bleiben gespeichert. Nicht gespeicherter Text kann verloren gehen.",
		"navigation.android.refreshQuestion": "Neu laden erzwingen?",
		"navigation.android.reload": "Seite neu laden",
		"navigation.android.serverHome": "Server-Startseite",
		"navigation.android.unsupportedState": "Nicht unterstützter Zustand der Android-App.",
		"peer.toast.unnamedInstance": "Eine Instanz",
		"session.notifications.activityTimeout": "{provider} gestoppt: seit 3 Stunden keine Aktivität",
		"session.notifications.agentStoppedBeforeAck": "Der Agent wurde gestoppt, bevor er den Empfang deiner Nachricht bestätigt hat.",
		"session.notifications.approval": "🖐️ {provider} braucht deine Genehmigung",
		"session.notifications.attention": "{provider} braucht deine Aufmerksamkeit",
		"session.notifications.body": "Projekt: {project}\nSitzung: {session}",
		"session.notifications.creditsRemaining": "{count} Guthabeneinheit übrig. | {count} Guthabeneinheiten übrig.",
		"session.notifications.creditsUsed": "{used} Guthabeneinheit verbraucht. | {used} Guthabeneinheiten verbraucht.",
		"session.notifications.creditsUsedOf": "{used} von {limit} Guthabeneinheit verbraucht. | {used} von {limit} Guthabeneinheiten verbraucht.",
		"session.notifications.enabledLabel": "Benachrichtigungen an",
		"session.notifications.ephemeral": "Flüchtige Sitzung beendet",
		"session.notifications.ephemeralSession": "Flüchtige Sitzung",
		"session.notifications.errors.update": "Sitzungsbenachrichtigungen konnten nicht aktualisiert werden",
		"session.notifications.extraUsageSentence": "{provider} greift derzeit auf dein Zusatznutzungs-Guthaben zurück, das zusätzlich zu deinem Tarif abgerechnet wird.",
		"session.notifications.extraUsageTitle": "{provider} – Zusatznutzung gestartet",
		"session.notifications.finished": "{provider} hat die Arbeit beendet",
		"session.notifications.instance": "eine Instanz",
		"session.notifications.invalidTitle": "Ungültiger Titel",
		"session.notifications.invalidTitleDetail": "Ungültiger Sitzungstitel: {error}",
		"session.notifications.invalidTitleUnknown": "Ungültiger Sitzungstitel: Unbekannter Fehler",
		"session.notifications.moneyUsed": "{used} {currency} verbraucht.",
		"session.notifications.moneyUsedOf": "{used} von {limit} {currency} verbraucht.",
		"session.notifications.mute": "Stummschalten",
		"session.notifications.muteTooltip": "Benachrichtigungen an – klicken, um die Benachrichtigung „Arbeit beendet“ stummzuschalten und diese Sitzung nicht mehr als ungelesen anzuzeigen",
		"session.notifications.mutedLabel": "Stummgeschaltet",
		"session.notifications.notConnected": "Nicht verbunden",
		"session.notifications.peerAccepted": "„{peer}“ hat deine Peer-Anfrage akzeptiert",
		"session.notifications.peerDelivered": "Deine Nachricht{title} an „{peer}“ wurde zugestellt",
		"session.notifications.peerDone": "„{peer}“ hat deine Nachricht{title} als erledigt markiert",
		"session.notifications.peerMessage": "Nachricht von {peer}",
		"session.notifications.peerRefused": "Deine Nachricht{title} an „{peer}“ wurde abgelehnt",
		"session.notifications.peerReply": "Antwort von {peer}",
		"session.notifications.peerRequest": "Peer-Anfrage",
		"session.notifications.peerRequestFrom": "Peer-Anfrage von {peer}",
		"session.notifications.peerVerification": "„{peer}“ hat deine Anfrage akzeptiert – schließe die Code-Verifizierung ab, um sie zu aktivieren",
		"session.notifications.providerDisabled": "Senden an {provider} nicht möglich: Der Anbieter ist deaktiviert. Aktiviere ihn unter Einstellungen → Anbieter.",
		"session.notifications.question": "🖐️ {provider} hat eine Frage an dich",
		"session.notifications.settingsInvalid": "Ein Einstellungswert ist ungültig.",
		"session.notifications.shells": "{count} Shell läuft noch im Hintergrund | {count} Shells laufen noch im Hintergrund",
		"session.notifications.startTimeout": "{provider} gestoppt: Start innerhalb von 1 Minute fehlgeschlagen",
		"session.notifications.terminated": "{provider} wegen eines Fehlers beendet",
		"session.notifications.turnFinished": "{provider} hat seinen Durchgang beendet",
		"session.notifications.unknownError": "Unbekannter Fehler",
		"session.notifications.unmuteTooltip": "Stummgeschaltet – klicken, um die Benachrichtigung „Arbeit beendet“ und die Markierung als ungelesen wiederherzustellen",
		"session.notifications.updateAvailable": "TwiCC v{version} ist verfügbar",
		"session.notifications.viewChanges": "Änderungen anzeigen",
		"session.speech.errors.cut": "Das Audio wurde vor dem Ende gestoppt",
		"session.speech.errors.gone": "Das Audio ist nicht mehr verfügbar",
		"session.speech.errors.identityRequired": "Eine Zusammenfassungs-Identität ist erforderlich",
		"session.speech.errors.initializing": "Native Sprachausgabe wird initialisiert. Versuche es erneut.",
		"session.speech.errors.nativeUnavailable": "Native Sprachausgabe ist nicht verfügbar. Öffne diese Seite erneut.",
		"session.speech.errors.noConfig": "Keine aktive Sprachausgabe-Konfiguration",
		"session.speech.errors.refused": "Der Browser hat die Wiedergabe des Audios abgelehnt",
		"session.speech.errors.requestFailed": "Anfrage fehlgeschlagen ({status})",
		"session.speech.errors.startFailed": "Die Sprachausgabe konnte nicht gestartet werden. Lade die Seite neu und versuche es erneut.",
		"session.speech.errors.summaryGone": "Die Zusammenfassung ist nicht mehr verfügbar",
		"session.speech.errors.unavailable": "Die Sprachausgabe ist nicht verfügbar. Lade die Seite neu und versuche es erneut.",
		"session.speech.errors.unreachable": "TwiCC ist nicht erreichbar",
		"settings.general.language.label": "Sprache",
		"settings.notifications.sounds.beep": "Beep",
		"settings.notifications.sounds.bloop": "Bloop",
		"settings.notifications.sounds.chime": "Glockenspiel",
		"settings.notifications.sounds.knock": "Klopfen",
		"settings.notifications.sounds.ping": "Ping",
		"settings.notifications.sounds.pop": "Pop",
		"settings.notifications.sounds.ripple": "Wellen",
		"settings.notifications.sounds.signal": "Signal",
		"settings.notifications.sounds.sweep": "Sweep",
		"settings.notifications.sounds.tink": "Tink",
		"settings.notifications.targets.extraUsage": "Zusatznutzung",
		"workspace.projectCount": "{count} Projekt | {count} Projekte"
	};
})), xh = /* @__PURE__ */ c({ default: () => yh }), Sh = o((() => {
	bh();
})), Ch = /* @__PURE__ */ s(((e, t) => {
	(function(n, r) {
		typeof e == "object" && t !== void 0 ? t.exports = r() : typeof define == "function" && define.amd ? define(r) : (n = typeof globalThis < "u" ? globalThis : n || self).dayjs = r();
	})(e, (function() {
		var e = 1e3, t = 6e4, n = 36e5, r = "millisecond", i = "second", a = "minute", o = "hour", s = "day", c = "week", l = "month", u = "quarter", d = "year", f = "date", p = "Invalid Date", m = /^(\d{4})[-/]?(\d{1,2})?[-/]?(\d{0,2})[Tt\s]*(\d{1,2})?:?(\d{1,2})?:?(\d{1,2})?[.:]?(\d+)?$/, h = /\[([^\]]+)]|YYYY|YY|M{1,4}|D{1,2}|d{1,4}|H{1,2}|h{1,2}|a|A|m{1,2}|s{1,2}|Z{1,2}|SSS/g, g = {
			name: "en",
			weekdays: "Sunday_Monday_Tuesday_Wednesday_Thursday_Friday_Saturday".split("_"),
			months: "January_February_March_April_May_June_July_August_September_October_November_December".split("_"),
			ordinal: function(e) {
				var t = [
					"th",
					"st",
					"nd",
					"rd"
				], n = e % 100;
				return "[" + e + (t[(n - 20) % 10] || t[n] || t[0]) + "]";
			}
		}, _ = function(e, t, n) {
			var r = String(e);
			return !r || r.length >= t ? e : "" + Array(t + 1 - r.length).join(n) + e;
		}, v = {
			s: _,
			z: function(e) {
				var t = -e.utcOffset(), n = Math.abs(t), r = Math.floor(n / 60), i = n % 60;
				return (t <= 0 ? "+" : "-") + _(r, 2, "0") + ":" + _(i, 2, "0");
			},
			m: function e(t, n) {
				if (t.date() < n.date()) return -e(n, t);
				var r = 12 * (n.year() - t.year()) + (n.month() - t.month()), i = t.clone().add(r, l), a = n - i < 0, o = t.clone().add(r + (a ? -1 : 1), l);
				return +(-(r + (n - i) / (a ? i - o : o - i)) || 0);
			},
			a: function(e) {
				return e < 0 ? Math.ceil(e) || 0 : Math.floor(e);
			},
			p: function(e) {
				return {
					M: l,
					y: d,
					w: c,
					d: s,
					D: f,
					h: o,
					m: a,
					s: i,
					ms: r,
					Q: u
				}[e] || String(e || "").toLowerCase().replace(/s$/, "");
			},
			u: function(e) {
				return e === void 0;
			}
		}, y = "en", b = {};
		b[y] = g;
		var x = "$isDayjsObject", S = function(e) {
			return e instanceof E || !(!e || !e[x]);
		}, C = function e(t, n, r) {
			var i;
			if (!t) return y;
			if (typeof t == "string") {
				var a = t.toLowerCase();
				b[a] && (i = a), n && (b[a] = n, i = a);
				var o = t.split("-");
				if (!i && o.length > 1) return e(o[0]);
			} else {
				var s = t.name;
				b[s] = t, i = s;
			}
			return !r && i && (y = i), i || !r && y;
		}, w = function(e, t) {
			if (S(e)) return e.clone();
			var n = typeof t == "object" ? t : {};
			return n.date = e, n.args = arguments, new E(n);
		}, T = v;
		T.l = C, T.i = S, T.w = function(e, t) {
			return w(e, {
				locale: t.$L,
				utc: t.$u,
				x: t.$x,
				$offset: t.$offset
			});
		};
		var E = function() {
			function g(e) {
				this.$L = C(e.locale, null, !0), this.parse(e), this.$x = this.$x || e.x || {}, this[x] = !0;
			}
			var _ = g.prototype;
			return _.parse = function(e) {
				this.$d = function(e) {
					var t = e.date, n = e.utc;
					if (t === null) return /* @__PURE__ */ new Date(NaN);
					if (T.u(t)) return /* @__PURE__ */ new Date();
					if (t instanceof Date) return new Date(t);
					if (typeof t == "string" && !/Z$/i.test(t)) {
						var r = t.match(m);
						if (r) {
							var i = r[2] - 1 || 0, a = (r[7] || "0").substring(0, 3);
							return n ? new Date(Date.UTC(r[1], i, r[3] || 1, r[4] || 0, r[5] || 0, r[6] || 0, a)) : new Date(r[1], i, r[3] || 1, r[4] || 0, r[5] || 0, r[6] || 0, a);
						}
					}
					return new Date(t);
				}(e), this.init();
			}, _.init = function() {
				var e = this.$d;
				this.$y = e.getFullYear(), this.$M = e.getMonth(), this.$D = e.getDate(), this.$W = e.getDay(), this.$H = e.getHours(), this.$m = e.getMinutes(), this.$s = e.getSeconds(), this.$ms = e.getMilliseconds();
			}, _.$utils = function() {
				return T;
			}, _.isValid = function() {
				return this.$d.toString() !== p;
			}, _.isSame = function(e, t) {
				var n = w(e);
				return this.startOf(t) <= n && n <= this.endOf(t);
			}, _.isAfter = function(e, t) {
				return w(e) < this.startOf(t);
			}, _.isBefore = function(e, t) {
				return this.endOf(t) < w(e);
			}, _.$g = function(e, t, n) {
				return T.u(e) ? this[t] : this.set(n, e);
			}, _.unix = function() {
				return Math.floor(this.valueOf() / 1e3);
			}, _.valueOf = function() {
				return this.$d.getTime();
			}, _.startOf = function(e, t) {
				var n = this, r = !!T.u(t) || t, u = T.p(e), p = function(e, t) {
					var i = T.w(n.$u ? Date.UTC(n.$y, t, e) : new Date(n.$y, t, e), n);
					return r ? i : i.endOf(s);
				}, m = function(e, t) {
					return T.w(n.toDate()[e].apply(n.toDate("s"), (r ? [
						0,
						0,
						0,
						0
					] : [
						23,
						59,
						59,
						999
					]).slice(t)), n);
				}, h = this.$W, g = this.$M, _ = this.$D, v = "set" + (this.$u ? "UTC" : "");
				switch (u) {
					case d: return r ? p(1, 0) : p(31, 11);
					case l: return r ? p(1, g) : p(0, g + 1);
					case c:
						var y = this.$locale().weekStart || 0, b = (h < y ? h + 7 : h) - y;
						return p(r ? _ - b : _ + (6 - b), g);
					case s:
					case f: return m(v + "Hours", 0);
					case o: return m(v + "Minutes", 1);
					case a: return m(v + "Seconds", 2);
					case i: return m(v + "Milliseconds", 3);
					default: return this.clone();
				}
			}, _.endOf = function(e) {
				return this.startOf(e, !1);
			}, _.$set = function(e, t) {
				var n, c = T.p(e), u = "set" + (this.$u ? "UTC" : ""), p = (n = {}, n[s] = u + "Date", n[f] = u + "Date", n[l] = u + "Month", n[d] = u + "FullYear", n[o] = u + "Hours", n[a] = u + "Minutes", n[i] = u + "Seconds", n[r] = u + "Milliseconds", n)[c], m = c === s ? this.$D + (t - this.$W) : t;
				if (c === l || c === d) {
					var h = this.clone().set(f, 1);
					h.$d[p](m), h.init(), this.$d = h.set(f, Math.min(this.$D, h.daysInMonth())).$d;
				} else p && this.$d[p](m);
				return this.init(), this;
			}, _.set = function(e, t) {
				return this.clone().$set(e, t);
			}, _.get = function(e) {
				return this[T.p(e)]();
			}, _.add = function(r, u) {
				var f, p = this;
				r = Number(r);
				var m = T.p(u), h = function(e) {
					var t = w(p);
					return T.w(t.date(t.date() + Math.round(e * r)), p);
				};
				if (m === l) return this.set(l, this.$M + r);
				if (m === d) return this.set(d, this.$y + r);
				if (m === s) return h(1);
				if (m === c) return h(7);
				var g = (f = {}, f[a] = t, f[o] = n, f[i] = e, f)[m] || 1, _ = this.$d.getTime() + r * g;
				return T.w(_, this);
			}, _.subtract = function(e, t) {
				return this.add(-1 * e, t);
			}, _.format = function(e) {
				var t = this, n = this.$locale();
				if (!this.isValid()) return n.invalidDate || p;
				var r = e || "YYYY-MM-DDTHH:mm:ssZ", i = T.z(this), a = this.$H, o = this.$m, s = this.$M, c = n.weekdays, l = n.months, u = n.meridiem, d = function(e, n, i, a) {
					return e && (e[n] || e(t, r)) || i[n].slice(0, a);
				}, f = function(e) {
					return T.s(a % 12 || 12, e, "0");
				}, m = u || function(e, t, n) {
					var r = e < 12 ? "AM" : "PM";
					return n ? r.toLowerCase() : r;
				};
				return r.replace(h, (function(e, r) {
					return r || function(e) {
						switch (e) {
							case "YY": return String(t.$y).slice(-2);
							case "YYYY": return T.s(t.$y, 4, "0");
							case "M": return s + 1;
							case "MM": return T.s(s + 1, 2, "0");
							case "MMM": return d(n.monthsShort, s, l, 3);
							case "MMMM": return d(l, s);
							case "D": return t.$D;
							case "DD": return T.s(t.$D, 2, "0");
							case "d": return String(t.$W);
							case "dd": return d(n.weekdaysMin, t.$W, c, 2);
							case "ddd": return d(n.weekdaysShort, t.$W, c, 3);
							case "dddd": return c[t.$W];
							case "H": return String(a);
							case "HH": return T.s(a, 2, "0");
							case "h": return f(1);
							case "hh": return f(2);
							case "a": return m(a, o, !0);
							case "A": return m(a, o, !1);
							case "m": return String(o);
							case "mm": return T.s(o, 2, "0");
							case "s": return String(t.$s);
							case "ss": return T.s(t.$s, 2, "0");
							case "SSS": return T.s(t.$ms, 3, "0");
							case "Z": return i;
						}
						return null;
					}(e) || i.replace(":", "");
				}));
			}, _.utcOffset = function() {
				return 15 * -Math.round(this.$d.getTimezoneOffset() / 15);
			}, _.diff = function(r, f, p) {
				var m, h = this, g = T.p(f), _ = w(r), v = (_.utcOffset() - this.utcOffset()) * t, y = this - _, b = function() {
					return T.m(h, _);
				};
				switch (g) {
					case d:
						m = b() / 12;
						break;
					case l:
						m = b();
						break;
					case u:
						m = b() / 3;
						break;
					case c:
						m = (y - v) / 6048e5;
						break;
					case s:
						m = (y - v) / 864e5;
						break;
					case o:
						m = y / n;
						break;
					case a:
						m = y / t;
						break;
					case i:
						m = y / e;
						break;
					default: m = y;
				}
				return p ? m : T.a(m);
			}, _.daysInMonth = function() {
				return this.endOf(l).$D;
			}, _.$locale = function() {
				return b[this.$L];
			}, _.locale = function(e, t) {
				if (!e) return this.$L;
				var n = this.clone(), r = C(e, t, !0);
				return r && (n.$L = r), n;
			}, _.clone = function() {
				return T.w(this.$d, this);
			}, _.toDate = function() {
				return new Date(this.valueOf());
			}, _.toJSON = function() {
				return this.isValid() ? this.toISOString() : null;
			}, _.toISOString = function() {
				return this.$d.toISOString();
			}, _.toString = function() {
				return this.$d.toUTCString();
			}, g;
		}(), D = E.prototype;
		return w.prototype = D, [
			["$ms", r],
			["$s", i],
			["$m", a],
			["$H", o],
			["$W", s],
			["$M", l],
			["$y", d],
			["$D", f]
		].forEach((function(e) {
			D[e[1]] = function(t) {
				return this.$g(t, e[0], e[1]);
			};
		})), w.extend = function(e, t) {
			return e.$i || (e(t, E, w), e.$i = !0), w;
		}, w.locale = C, w.isDayjs = S, w.unix = function(e) {
			return w(1e3 * e);
		}, w.en = b[y], w.Ls = b, w.p = {}, w;
	}));
})), wh = /* @__PURE__ */ s(((e, t) => {
	(function(n, r) {
		typeof e == "object" && t !== void 0 ? t.exports = r(Ch()) : typeof define == "function" && define.amd ? define(["dayjs"], r) : (n = typeof globalThis < "u" ? globalThis : n || self).dayjs_locale_de = r(n.dayjs);
	})(e, (function(e) {
		function t(e) {
			return e && typeof e == "object" && "default" in e ? e : { default: e };
		}
		var n = t(e), r = {
			s: "ein paar Sekunden",
			m: ["eine Minute", "einer Minute"],
			mm: "%d Minuten",
			h: ["eine Stunde", "einer Stunde"],
			hh: "%d Stunden",
			d: ["ein Tag", "einem Tag"],
			dd: ["%d Tage", "%d Tagen"],
			M: ["ein Monat", "einem Monat"],
			MM: ["%d Monate", "%d Monaten"],
			y: ["ein Jahr", "einem Jahr"],
			yy: ["%d Jahre", "%d Jahren"]
		};
		function i(e, t, n) {
			var i = r[n];
			return Array.isArray(i) && (i = i[+!t]), i.replace("%d", e);
		}
		var a = {
			name: "de",
			weekdays: "Sonntag_Montag_Dienstag_Mittwoch_Donnerstag_Freitag_Samstag".split("_"),
			weekdaysShort: "So._Mo._Di._Mi._Do._Fr._Sa.".split("_"),
			weekdaysMin: "So_Mo_Di_Mi_Do_Fr_Sa".split("_"),
			months: "Januar_Februar_März_April_Mai_Juni_Juli_August_September_Oktober_November_Dezember".split("_"),
			monthsShort: "Jan._Feb._März_Apr._Mai_Juni_Juli_Aug._Sept._Okt._Nov._Dez.".split("_"),
			ordinal: function(e) {
				return e + ".";
			},
			weekStart: 1,
			yearStart: 4,
			formats: {
				LTS: "HH:mm:ss",
				LT: "HH:mm",
				L: "DD.MM.YYYY",
				LL: "D. MMMM YYYY",
				LLL: "D. MMMM YYYY HH:mm",
				LLLL: "dddd, D. MMMM YYYY HH:mm"
			},
			relativeTime: {
				future: "in %s",
				past: "vor %s",
				s: i,
				m: i,
				mm: i,
				h: i,
				hh: i,
				d: i,
				dd: i,
				M: i,
				MM: i,
				y: i,
				yy: i
			}
		};
		return n.default.locale(a, null, !0), a;
	}));
}));
//#endregion
//#region node_modules/@shoelace-style/localize/dist/index.js
function Th(...e) {
	e.map((e) => {
		let t = e.$code.toLowerCase();
		Oh.has(t) ? Oh.set(t, Object.assign(Object.assign({}, Oh.get(t)), e)) : Oh.set(t, e), kh || (kh = e);
	}), Eh();
}
function Eh() {
	Mh && (Ah = document.documentElement.dir || "ltr", jh = document.documentElement.lang || navigator.language), [...Dh.keys()].map((e) => {
		typeof e.requestUpdate == "function" && e.requestUpdate();
	});
}
var Dh, Oh, kh, Ah, jh, Mh, Nh = o((() => {
	if (Dh = /* @__PURE__ */ new Set(), Oh = /* @__PURE__ */ new Map(), Mh = typeof MutationObserver < "u" && typeof document < "u" && document.documentElement !== void 0, Mh) {
		let e = new MutationObserver(Eh);
		document.documentElement.dir, document.documentElement.lang || navigator.language, e.observe(document.documentElement, {
			attributes: !0,
			attributeFilter: ["dir", "lang"]
		});
	}
})), Ph, Fh, Ih = o((() => {
	Nh(), Ph = {
		$code: "en",
		$name: "English",
		$dir: "ltr",
		carousel: "Carousel",
		clearEntry: "Clear entry",
		close: "Close",
		copied: "Copied",
		copy: "Copy",
		currentValue: "Current value",
		dropFileHere: "Drop file here or click to browse",
		decrement: "Decrement",
		dropFilesHere: "Drop files here or click to browse",
		error: "Error",
		goToSlide: (e, t) => `Go to slide ${e} of ${t}`,
		hidePassword: "Hide password",
		increment: "Increment",
		loading: "Loading",
		nextSlide: "Next slide",
		numOptionsSelected: (e) => e === 0 ? "No options selected" : e === 1 ? "1 option selected" : `${e} options selected`,
		pauseAnimation: "Pause animation",
		playAnimation: "Play animation",
		previousSlide: "Previous slide",
		progress: "Progress",
		remove: "Remove",
		resize: "Resize",
		scrollableRegion: "Scrollable region",
		scrollToEnd: "Scroll to end",
		scrollToStart: "Scroll to start",
		selectAColorFromTheScreen: "Select a color from the screen",
		showPassword: "Show password",
		slideNum: (e) => `Slide ${e}`,
		toggleColorFormat: "Toggle color format",
		zoomIn: "Zoom in",
		zoomOut: "Zoom out"
	}, Th(Ph), Fh = Ph;
})), Lh = o((() => {
	Ih(), Nh(), Th(Fh);
})), Rh, zh, Bh = o((() => {
	Lh(), Rh = {
		$code: "de",
		$name: "Deutsch",
		$dir: "ltr",
		carousel: "Karussell",
		clearEntry: "Eingabe löschen",
		close: "Schließen",
		copied: "Kopiert",
		copy: "Kopieren",
		currentValue: "Aktueller Wert",
		decrement: "Verringern",
		dropFileHere: "Datei hier ablegen oder zum Durchsuchen klicken",
		dropFilesHere: "Dateien hier ablegen oder zum Durchsuchen klicken",
		error: "Fehler",
		goToSlide: (e, t) => `Zu Folie ${e} von ${t} gehen`,
		hidePassword: "Passwort verbergen",
		increment: "Erhöhen",
		loading: "Wird geladen",
		nextSlide: "Nächste Folie",
		numOptionsSelected: (e) => e === 0 ? "Keine Optionen ausgewählt" : e === 1 ? "1 Option ausgewählt" : `${e} Optionen ausgew\xE4hlt`,
		pauseAnimation: "Animation pausieren",
		playAnimation: "Animation abspielen",
		previousSlide: "Vorherige Folie",
		progress: "Fortschritt",
		remove: "Entfernen",
		resize: "Größe ändern",
		scrollableRegion: "Scrollbarer Bereich",
		scrollToEnd: "Zum Ende scrollen",
		scrollToStart: "Zum Anfang scrollen",
		selectAColorFromTheScreen: "Farbe vom Bildschirm auswählen",
		showPassword: "Passwort anzeigen",
		slideNum: (e) => `Folie ${e}`,
		toggleColorFormat: "Farbformat wechseln",
		zoomIn: "Hineinzoomen",
		zoomOut: "Herauszoomen"
	}, Th(Rh), zh = Rh;
})), Vh = /* @__PURE__ */ c({ default: () => zh }), Hh = o((() => {
	Bh(), Lh(), Ih();
})), Uh, Wh = o((() => {
	Uh = {
		"android.browser.errors.applicationUnavailable": "Ninguna aplicación puede abrir este enlace.",
		"android.browser.errors.browserUnavailable": "No hay ningún navegador disponible para abrir este enlace.",
		"android.browser.errors.certificate": "No se pudo verificar el certificado HTTPS. Comprueba el certificado del túnel y la fecha del teléfono.",
		"android.browser.errors.engineStart": "{engine} no pudo iniciarse. Comprueba la instalación del motor y vuelve a abrir TwiCC.",
		"android.browser.errors.httpStatus": "El servidor devolvió HTTP {status}. Comprueba el túnel o el servidor y reintenta.",
		"android.browser.errors.instanceRemoved": "Esta instancia ya no está guardada.",
		"android.browser.errors.linkUnsupported": "Este tipo de enlace no es compatible.",
		"android.browser.errors.pageBlocked": "Esta página no se puede abrir dentro de TwiCC. Reintenta para volver a tu servidor.",
		"android.browser.errors.rendererStopped": "La página se detuvo inesperadamente. Reintenta para volver a abrir TwiCC.",
		"android.browser.errors.sessionClosed": "La sesión del navegador está cerrada.",
		"android.browser.errors.unreachable": "No se puede acceder a {name}. Comprueba tu conexión y el túnel HTTPS y reintenta.",
		"android.browser.errors.webviewProfiles": "Actualiza Android System WebView o Chrome para usar instancias separadas.",
		"android.browser.menuDescription": "Menú de la aplicación Android para {name}",
		"android.browser.version": "TwiCC {version} · {engine}",
		"android.downloads.browserRequired": "Esta descarga necesita un navegador. Abre la página en tu navegador para guardar exportaciones blob o data.",
		"android.downloads.errors.failed": "La descarga falló. Comprueba tu conexión y reintenta.",
		"android.downloads.errors.saveUnavailable": "No se puede guardar esta descarga. Intenta abrir la página en tu navegador.",
		"android.downloads.errors.startFailed": "La descarga no pudo iniciarse. Intenta abrir esta página en tu navegador.",
		"android.downloads.saved": "{filename} se guardó en Descargas.",
		"android.downloads.saving": "Guardando {filename} en Descargas…",
		"android.downloads.started": "Descarga iniciada. Revisa la notificación de descargas de Android.",
		"android.files.errors.folderUnsupported": "No se pueden subir carpetas. Selecciona archivos individuales.",
		"android.files.errors.pickerUnavailable": "No hay ningún selector de archivos disponible.",
		"android.files.errors.readFailed": "No se pudieron leer los archivos seleccionados. Reintenta.",
		"android.instances.add": "Añadir instancia",
		"android.instances.addressHint": "Solo HTTPS. Una dirección nueva requiere iniciar sesión de nuevo.",
		"android.instances.addressLabel": "Dirección del servidor",
		"android.instances.editDescription": "Editar {name}",
		"android.instances.editTitle": "Editar instancia",
		"android.instances.emptyBody": "Añade la dirección de tu servidor TwiCC para empezar.",
		"android.instances.emptyTitle": "Aún no hay instancias",
		"android.instances.errors.duplicate": "Esta dirección de servidor ya está guardada.",
		"android.instances.errors.httpsRequired": "Usa una dirección HTTPS, como https://twicc.example.com.",
		"android.instances.errors.invalidAddress": "Introduce una dirección HTTPS válida.",
		"android.instances.errors.invalidPort": "Introduce un número de puerto válido.",
		"android.instances.errors.originRequired": "Usa la dirección del servidor sin credenciales, consulta ni fragmento.",
		"android.instances.errors.rootRequired": "Usa la dirección raíz del servidor, sin ruta de página.",
		"android.instances.lastUsed": "Última utilizada",
		"android.instances.nameLabel": "Nombre (opcional)",
		"android.instances.openDescription": "Abrir {name}, {address}",
		"android.instances.remove": "Quitar",
		"android.instances.removeDescription": "Quitar {name}",
		"android.instances.removeMessage": "Quita esta dirección y solicita eliminar su inicio de sesión y almacenamiento web en cada motor. El servidor no cambia.",
		"android.instances.removeTitle": "¿Quitar {name}?",
		"android.instances.saved": "Instancias guardadas",
		"android.instances.subtitle": "Conéctate a tu servidor TwiCC existente mediante un túnel HTTPS.",
		"android.instances.title": "TwiCC",
		"android.language.errors.changed": "El idioma de la aplicación cambió. Selecciona el idioma de nuevo.",
		"android.language.errors.invalidRevision": "La solicitud de idioma de la aplicación no es válida. Recarga esta página e inténtalo de nuevo.",
		"android.language.errors.revisionExhausted": "El idioma de la aplicación no se puede cambiar.",
		"android.language.errors.saveFailed": "No se pudo guardar el idioma de la aplicación. Inténtalo de nuevo.",
		"android.language.errors.staleDocument": "Recarga esta página antes de cambiar el idioma de la aplicación.",
		"android.language.errors.unsupported": "Este idioma no está disponible en la aplicación Android. Actualiza la aplicación y reintenta.",
		"android.language.errors.updateApp": "Actualiza la aplicación Android para sincronizar el idioma de la interfaz.",
		"android.language.errors.updateServer": "Actualiza este servidor TwiCC para sincronizar el idioma de la interfaz.",
		"android.push.channels.attention": "Preguntas y aprobaciones",
		"android.push.channels.finished": "Trabajo finalizado",
		"android.push.channels.peer": "Mensajes y solicitudes de pares",
		"android.push.distributorLabel": "Notificaciones de TwiCC",
		"android.push.errors.updateApp": "Las notificaciones de Android no están disponibles. Actualiza la aplicación Android y reintenta.",
		"android.push.errors.updateServer": "Actualiza este servidor TwiCC para usar notificaciones de Android detalladas. Hasta entonces, las notificaciones muestran un mensaje genérico.",
		"android.push.extraUsageBody": "{sentence}\n{usage}",
		"android.push.finishedWithShells": "{finished} — {shells}",
		"android.push.genericBody": "Abre TwiCC para ver esta notificación.",
		"android.push.genericTitle": "Notificación de TwiCC",
		"android.push.peerBody": "«{title}»\n{preview}",
		"android.push.peerBodyWithProject": "{message}\nProyecto: {project}",
		"android.push.peerBodyWithSession": "{message}\nSesión: {session}",
		"android.push.peerRequestBody": "{peer} quiere emparejarse con tu instancia.\nDirección: {address}",
		"android.push.projectWithParent": "{parent} › {project}",
		"android.push.soundTitle": "TwiCC — {sound}",
		"android.push.titleWithInstance": "{title} · {instance}",
		"android.speech.batteryAdvice": "Puedes escuchar la cronología sin cambiar los ajustes de batería. Android puede retrasar o interrumpir la lectura en segundo plano o con la pantalla apagada. Permite el uso de batería sin restricciones para TwiCC para mejorar la escucha en segundo plano. Puedes cambiarlo después en Ajustes de Android → Aplicaciones → TwiCC → Batería.\n\nContinúa sin cambios para empezar ahora, o abre Ajustes y vuelve a activar la escucha.",
		"android.speech.batteryTitle": "Escucha de la cronología y batería",
		"android.speech.channel": "Lectura de la cronología",
		"android.speech.continueWithoutChanges": "Continuar sin cambios",
		"android.speech.errors.androidStopped": "Android detuvo la lectura. Iníciala de nuevo.",
		"android.speech.errors.audioEmpty": "El audio TTS está vacío",
		"android.speech.errors.audioFormat": "Formato de audio de lectura no compatible",
		"android.speech.errors.audioRequest": "La solicitud TTS falló ({status})",
		"android.speech.errors.batteryChoiceRequired": "Elige cómo continuar en el diálogo de consejos de batería.",
		"android.speech.errors.changeFailed": "No se pudo cambiar la reproducción de lectura. Recarga la página y reintenta.",
		"android.speech.errors.cookieSupport": "La lectura nativa requiere un WebView compatible con GET_COOKIE_INFO. Actualiza Android System WebView.",
		"android.speech.errors.credentialsUnavailable": "Vuelve a abrir esta instancia e inicia la lectura de nuevo.",
		"android.speech.errors.failed": "La lectura falló",
		"android.speech.errors.generationFailed": "Falló la generación de lectura",
		"android.speech.errors.instanceChanged": "La instancia activa cambió.",
		"android.speech.errors.instanceClosed": "La instancia activa se cerró. Inicia la lectura de nuevo para continuar.",
		"android.speech.errors.instanceRequired": "Abre una instancia guardada antes de iniciar la lectura.",
		"android.speech.errors.interrupted": "La reproducción de audio se interrumpió.",
		"android.speech.errors.playbackChanged": "La reproducción de lectura cambió. Reintenta.",
		"android.speech.errors.playbackFailed": "La reproducción de audio no pudo iniciarse.",
		"android.speech.errors.retryPlayback": "No se pudo acceder a TwiCC. Inicia la reproducción de nuevo.",
		"android.speech.errors.staleDocument": "Recarga esta página y vuelve a intentar la lectura.",
		"android.speech.errors.staleSession": "Esta notificación pertenece a una sesión de lectura anterior.",
		"android.speech.errors.stopping": "La lectura se está deteniendo. Espera un momento y reintenta.",
		"android.speech.errors.updateRequired": "La lectura no está disponible. Actualiza la aplicación Android y reintenta.",
		"android.speech.errors.visibleRequired": "Abre TwiCC antes de iniciar la lectura.",
		"android.speech.notificationTitle": "{name} · Lectura",
		"android.speech.notificationsRequired": "Activa las notificaciones de lectura para usar Omitir y Detener todo en segundo plano. Después, inicia la lectura de nuevo.",
		"android.speech.openSettings": "Abrir Ajustes",
		"android.speech.pauseTimeline": "Pausar",
		"android.speech.preparing": "Preparando",
		"android.speech.reading": "Leyendo",
		"android.speech.reconnecting": "Reconectando",
		"android.speech.recoveryAnnouncement": "Conexión restablecida. Los mensajes recibidos durante la interrupción no se leerán. La lectura automática se reanuda.",
		"android.speech.resumeTimeline": "Reanudar",
		"android.speech.settingsTitle": "Se requieren ajustes de lectura",
		"android.speech.skip": "Omitir",
		"android.speech.stopAll": "Detener",
		"android.speech.stopping": "Deteniendo",
		"android.speech.timelinePaused": "Lectura de la cronología en pausa",
		"android.speech.waiting": "En espera",
		"backend.tts.adaptationStopped": "La adaptación del texto se detuvo antes del final",
		"backend.tts.apiKeyRequired": "TWICC_OPENROUTER_API_KEY no está definida. Añádela a {path} y reinicia TwiCC.",
		"backend.tts.audioCut": "El audio se detuvo antes del final",
		"backend.tts.audioExpired": "El audio expiró antes de la reproducción",
		"backend.tts.audioGone": "El audio ya no está disponible",
		"backend.tts.authenticationExpired": "La autenticación expiró. Abre TwiCC e inicia sesión de nuevo.",
		"backend.tts.catchupIncomplete": "No se pudieron cargar algunos mensajes. Activa de nuevo la lectura de la cronología.",
		"backend.tts.chooseMp3": "La salida del script parece MP3: elige MP3",
		"backend.tts.chooseWav": "La salida del script parece WAV: elige WAV",
		"backend.tts.commandMissing": "Esta configuración de script no tiene comando",
		"backend.tts.configMatches": "'config_id' debe ser igual a 'script_id'",
		"backend.tts.configNotSaved": "Esta configuración aún no está guardada: inténtalo de nuevo",
		"backend.tts.configNotValidated": "Esta configuración de script no está validada: pruébala de nuevo en la configuración",
		"backend.tts.configurationUnavailable": "La síntesis de voz no está disponible. Revisa la configuración activa.",
		"backend.tts.failed": "Falló la síntesis de voz",
		"backend.tts.fieldString": "'{field}' debe ser una cadena",
		"backend.tts.flacUnsupported": "La salida del script parece audio FLAC, que TwiCC no reproduce",
		"backend.tts.formatChoices": "'response_format' debe ser uno de: {choices}",
		"backend.tts.invalidCommand": "No se pudo iniciar la voz. Recarga la página e inténtalo de nuevo.",
		"backend.tts.modelRequired": "'model' debe ser una cadena no vacía",
		"backend.tts.mp4Unsupported": "La salida del script parece audio MP4, que TwiCC no reproduce",
		"backend.tts.noAudio": "El script no generó audio",
		"backend.tts.notMp3": "La salida del script no es MP3",
		"backend.tts.notWav": "La salida del script no es WAV",
		"backend.tts.nothingToRead": "No hay nada que leer en voz alta en este texto",
		"backend.tts.oggUnsupported": "La salida del script parece audio Ogg, que TwiCC no reproduce",
		"backend.tts.openrouterStatus": "OpenRouter devolvió HTTP {status}",
		"backend.tts.openrouterTimeout": "OpenRouter no respondió a tiempo",
		"backend.tts.openrouterUnreachable": "No se pudo conectar con OpenRouter",
		"backend.tts.originChoices": "'origin' debe ser uno de: {choices}",
		"backend.tts.partFormatChanged": "El formato del audio cambió entre dos partes del texto",
		"backend.tts.pcmRateRequired": "Esta configuración de script necesita una frecuencia de muestreo para PCM",
		"backend.tts.provider400Hint": "El proveedor devolvió 400. Revisa el modelo, la voz, el formato y la longitud del texto.",
		"backend.tts.scopeUnavailable": "La lectura de la cronología se detuvo. Elige otra cronología.",
		"backend.tts.scriptAudioLarge": "El audio del script es demasiado grande",
		"backend.tts.scriptExit": "El script falló (estado de salida {status})",
		"backend.tts.scriptId": "'script_id' debe ser una cadena no vacía",
		"backend.tts.scriptMissing": "Esta configuración de script no existe",
		"backend.tts.scriptOutputPath": "La salida del script no es una única ruta absoluta",
		"backend.tts.scriptPathReused": "El script devolvió la misma ruta dos veces",
		"backend.tts.scriptSignal": "El script se detuvo por la señal {signal}",
		"backend.tts.scriptStalled": "El script dejó de enviar audio",
		"backend.tts.scriptStart": "No se pudo iniciar el script",
		"backend.tts.scriptUnreadable": "No se puede leer el archivo del script",
		"backend.tts.sessionLost": "La voz se detuvo. Inicia la reproducción o activa de nuevo la lectura de la cronología.",
		"backend.tts.speedNumber": "'speed' debe ser un número positivo finito",
		"backend.tts.stopped": "La síntesis de voz se detuvo",
		"backend.tts.summaryChanged": "El resumen guardado cambió. Actualízalo antes de leerlo.",
		"backend.tts.summaryFingerprint": "'summary_result_hash' debe ser la huella del resultado guardado",
		"backend.tts.summaryGone": "El resumen ya no está disponible",
		"backend.tts.summaryHandle": "'summary_id' debe ser un identificador no vacío",
		"backend.tts.summaryHashRequiresSaved": "'summary_result_hash' requiere 'saved_summary_id'",
		"backend.tts.summaryReference": "Un resumen necesita exactamente una referencia, sin texto ni prueba de configuración",
		"backend.tts.summarySavedGone": "El resumen guardado ya no está disponible",
		"backend.tts.summarySavedId": "'saved_summary_id' debe ser un entero positivo",
		"backend.tts.summaryStopped": "El resumen se detuvo antes del final",
		"backend.tts.textRequired": "'text' debe ser una cadena no vacía",
		"backend.tts.textTooLong": "El texto es demasiado largo para enviarlo",
		"backend.tts.wav16bit": "El WAV del script no es PCM de 16 bits",
		"backend.tts.wavFormat": "El WAV del script tiene una frecuencia de muestreo o un número de canales no compatible",
		"common.actions.cancel": "Cancelar",
		"common.actions.ok": "Aceptar",
		"common.actions.retry": "Reintentar",
		"common.actions.save": "Guardar",
		"common.android.errors.actionFailed": "No se pudo completar esta acción. Recarga esta página e inténtalo de nuevo.",
		"common.android.errors.closed": "Se cerró la conexión con la aplicación de Android. Recarga esta página e inténtalo de nuevo.",
		"common.android.errors.invalidNotification": "Destino de notificación no válido.",
		"common.android.errors.speechPreparing": "La voz se está preparando. Inténtalo de nuevo en un momento.",
		"common.android.errors.tooLarge": "Esta acción contiene demasiados datos. Prueba con una selección más pequeña.",
		"common.android.errors.unreachable": "No se pudo contactar con la aplicación de Android. Recarga esta página e inténtalo de nuevo.",
		"common.labels.unknown": "Desconocido",
		"common.sessionCount": "{count} sesión | {count} sesiones",
		"composer.status.startingProvider": "Iniciando el proceso de {provider}…",
		"navigation.android.active": "Activa",
		"navigation.android.browser": "Abrir en el navegador",
		"navigation.android.forceRefresh": "Recargar página (sin caché)",
		"navigation.android.manage": "Administrar instancias",
		"navigation.android.menu": "Menú de la aplicación Android",
		"navigation.android.refresh": "Actualizar",
		"navigation.android.refreshHint": "Recarga esta página y omite su caché de recursos. Tu inicio de sesión y tus preferencias se conservan. Puedes perder el texto sin guardar.",
		"navigation.android.refreshQuestion": "¿Forzar actualización?",
		"navigation.android.reload": "Recargar página",
		"navigation.android.serverHome": "Inicio del servidor",
		"navigation.android.unsupportedState": "Estado de la aplicación Android no compatible.",
		"peer.toast.unnamedInstance": "Una instancia",
		"session.notifications.activityTimeout": "{provider} se detuvo: sin actividad durante 3 horas",
		"session.notifications.agentStoppedBeforeAck": "El agente se detuvo antes de confirmar la recepción de tu mensaje.",
		"session.notifications.approval": "🖐️ {provider} necesita tu aprobación",
		"session.notifications.attention": "{provider} necesita tu atención",
		"session.notifications.body": "Proyecto: {project}\nSesión: {session}",
		"session.notifications.creditsRemaining": "Queda {count} crédito. | Quedan {count} créditos.",
		"session.notifications.creditsUsed": "Se usó {used} crédito. | Se usaron {used} créditos.",
		"session.notifications.creditsUsedOf": "Se usó {used} de {limit} crédito. | Se usaron {used} de {limit} créditos.",
		"session.notifications.enabledLabel": "Notificaciones activadas",
		"session.notifications.ephemeral": "Sesión efímera terminada",
		"session.notifications.ephemeralSession": "Sesión efímera",
		"session.notifications.errors.update": "No se pudieron actualizar las notificaciones de la sesión",
		"session.notifications.extraUsageSentence": "{provider} está usando actualmente tu crédito de uso adicional, que se factura aparte de tu plan.",
		"session.notifications.extraUsageTitle": "{provider} — Comenzó el uso adicional",
		"session.notifications.finished": "{provider} terminó de trabajar",
		"session.notifications.instance": "una instancia",
		"session.notifications.invalidTitle": "Título no válido",
		"session.notifications.invalidTitleDetail": "Título de sesión no válido: {error}",
		"session.notifications.invalidTitleUnknown": "Título de sesión no válido: error desconocido",
		"session.notifications.moneyUsed": "{used} {currency} usados.",
		"session.notifications.moneyUsedOf": "{used} de {limit} {currency} usados.",
		"session.notifications.mute": "Silenciar",
		"session.notifications.muteTooltip": "Notificaciones activadas — haz clic para silenciar la notificación de “terminó de trabajar” y dejar de marcar esta sesión como no leída",
		"session.notifications.mutedLabel": "Silenciada",
		"session.notifications.notConnected": "Sin conexión",
		"session.notifications.peerAccepted": "\"{peer}\" aceptó tu solicitud de par",
		"session.notifications.peerDelivered": "Tu mensaje{title} a \"{peer}\" fue entregado",
		"session.notifications.peerDone": "\"{peer}\" marcó tu mensaje{title} como terminado",
		"session.notifications.peerMessage": "Mensaje de {peer}",
		"session.notifications.peerRefused": "Tu mensaje{title} a \"{peer}\" fue rechazado",
		"session.notifications.peerReply": "Respuesta de {peer}",
		"session.notifications.peerRequest": "Solicitud de par",
		"session.notifications.peerRequestFrom": "Solicitud de par de {peer}",
		"session.notifications.peerVerification": "\"{peer}\" aceptó tu solicitud — completa la verificación del código para activar la conexión",
		"session.notifications.providerDisabled": "No se puede enviar a {provider}: está desactivado. Actívalo en Configuración → Proveedores.",
		"session.notifications.question": "🖐️ {provider} tiene una pregunta para ti",
		"session.notifications.settingsInvalid": "Un valor de la configuración no es válido.",
		"session.notifications.shells": "{count} shell en segundo plano sigue en ejecución | {count} shells en segundo plano siguen en ejecución",
		"session.notifications.startTimeout": "{provider} se detuvo: no pudo iniciarse en 1 minuto",
		"session.notifications.terminated": "{provider} terminó por un error",
		"session.notifications.turnFinished": "{provider} terminó su turno",
		"session.notifications.unknownError": "Error desconocido",
		"session.notifications.unmuteTooltip": "Silenciada — haz clic para restaurar la notificación de “terminó de trabajar” y el indicador de no leído",
		"session.notifications.updateAvailable": "TwiCC v{version} está disponible",
		"session.notifications.viewChanges": "Ver cambios",
		"session.speech.errors.cut": "El audio se detuvo antes del final",
		"session.speech.errors.gone": "El audio ya no está disponible",
		"session.speech.errors.identityRequired": "Se requiere una identidad de resumen",
		"session.speech.errors.initializing": "La voz nativa se está iniciando. Inténtalo de nuevo.",
		"session.speech.errors.nativeUnavailable": "La voz nativa no está disponible. Vuelve a abrir esta página.",
		"session.speech.errors.noConfig": "No hay ninguna configuración activa de texto a voz",
		"session.speech.errors.refused": "El navegador rechazó reproducir el audio",
		"session.speech.errors.requestFailed": "Falló la solicitud ({status})",
		"session.speech.errors.startFailed": "No se pudo iniciar la voz. Recarga la página e inténtalo de nuevo.",
		"session.speech.errors.summaryGone": "El resumen ya no está disponible",
		"session.speech.errors.unavailable": "La voz no está disponible. Recarga la página e inténtalo de nuevo.",
		"session.speech.errors.unreachable": "No se pudo conectar con TwiCC",
		"settings.general.language.label": "Idioma",
		"settings.notifications.sounds.beep": "Beep",
		"settings.notifications.sounds.bloop": "Bloop",
		"settings.notifications.sounds.chime": "Chime",
		"settings.notifications.sounds.knock": "Knock",
		"settings.notifications.sounds.ping": "Ping",
		"settings.notifications.sounds.pop": "Pop",
		"settings.notifications.sounds.ripple": "Ripple",
		"settings.notifications.sounds.signal": "Signal",
		"settings.notifications.sounds.sweep": "Sweep",
		"settings.notifications.sounds.tink": "Tink",
		"settings.notifications.targets.extraUsage": "Uso adicional",
		"workspace.projectCount": "{count} proyecto | {count} proyectos"
	};
})), Gh = /* @__PURE__ */ c({ default: () => Uh }), Kh = o((() => {
	Wh();
})), qh = /* @__PURE__ */ s(((e, t) => {
	(function(n, r) {
		typeof e == "object" && t !== void 0 ? t.exports = r(Ch()) : typeof define == "function" && define.amd ? define(["dayjs"], r) : (n = typeof globalThis < "u" ? globalThis : n || self).dayjs_locale_es = r(n.dayjs);
	})(e, (function(e) {
		function t(e) {
			return e && typeof e == "object" && "default" in e ? e : { default: e };
		}
		var n = t(e), r = {
			name: "es",
			monthsShort: "ene_feb_mar_abr_may_jun_jul_ago_sep_oct_nov_dic".split("_"),
			weekdays: "domingo_lunes_martes_miércoles_jueves_viernes_sábado".split("_"),
			weekdaysShort: "dom._lun._mar._mié._jue._vie._sáb.".split("_"),
			weekdaysMin: "do_lu_ma_mi_ju_vi_sá".split("_"),
			months: "enero_febrero_marzo_abril_mayo_junio_julio_agosto_septiembre_octubre_noviembre_diciembre".split("_"),
			weekStart: 1,
			formats: {
				LT: "H:mm",
				LTS: "H:mm:ss",
				L: "DD/MM/YYYY",
				LL: "D [de] MMMM [de] YYYY",
				LLL: "D [de] MMMM [de] YYYY H:mm",
				LLLL: "dddd, D [de] MMMM [de] YYYY H:mm"
			},
			relativeTime: {
				future: "en %s",
				past: "hace %s",
				s: "unos segundos",
				m: "un minuto",
				mm: "%d minutos",
				h: "una hora",
				hh: "%d horas",
				d: "un día",
				dd: "%d días",
				M: "un mes",
				MM: "%d meses",
				y: "un año",
				yy: "%d años"
			},
			ordinal: function(e) {
				return e + "º";
			}
		};
		return n.default.locale(r, null, !0), r;
	}));
})), Jh = /* @__PURE__ */ c({ default: () => Xh }), Yh, Xh, Zh = o((() => {
	Lh(), Ih(), Yh = {
		$code: "es",
		$name: "Español",
		$dir: "ltr",
		carousel: "Carrusel",
		clearEntry: "Borrar entrada",
		close: "Cerrar",
		copied: "Copiado",
		copy: "Copiar",
		currentValue: "Valor actual",
		decrement: "Disminuir",
		dropFileHere: "Drop file here or click to browse",
		dropFilesHere: "Drop files here or click to browse",
		error: "Error",
		goToSlide: (e, t) => `Ir a la diapositiva ${e} de ${t}`,
		hidePassword: "Ocultar contraseña",
		increment: "Aumentar",
		loading: "Cargando",
		nextSlide: "Siguiente diapositiva",
		numOptionsSelected: (e) => e === 0 ? "No hay opciones seleccionadas" : e === 1 ? "1 opción seleccionada" : `${e} opci\xF3n seleccionada`,
		pauseAnimation: "Pausar animación",
		playAnimation: "Reproducir animación",
		previousSlide: "Diapositiva anterior",
		progress: "Progreso",
		remove: "Eliminar",
		resize: "Cambiar el tamaño",
		scrollableRegion: "Región desplazable",
		scrollToEnd: "Desplazarse hasta el final",
		scrollToStart: "Desplazarse al inicio",
		selectAColorFromTheScreen: "Seleccione un color de la pantalla",
		showPassword: "Mostrar contraseña",
		slideNum: (e) => `Diapositiva ${e}`,
		toggleColorFormat: "Alternar formato de color",
		zoomIn: "Acercar",
		zoomOut: "Alejar"
	}, Th(Yh), Xh = Yh;
})), Qh, $h = o((() => {
	Qh = {
		"android.browser.errors.applicationUnavailable": "Aucune application ne peut ouvrir ce lien.",
		"android.browser.errors.browserUnavailable": "Aucun navigateur n’est disponible pour ouvrir ce lien.",
		"android.browser.errors.certificate": "Le certificat HTTPS ne peut pas être vérifié. Vérifiez le certificat du tunnel et la date de votre téléphone.",
		"android.browser.errors.engineStart": "{engine} ne peut pas démarrer. Vérifiez l’installation du moteur, puis rouvrez TwiCC.",
		"android.browser.errors.httpStatus": "Le serveur a renvoyé HTTP {status}. Vérifiez votre tunnel ou votre serveur, puis réessayez.",
		"android.browser.errors.instanceRemoved": "Cette instance n’est plus enregistrée.",
		"android.browser.errors.linkUnsupported": "Ce type de lien n’est pas pris en charge.",
		"android.browser.errors.pageBlocked": "Cette page ne peut pas s’ouvrir dans TwiCC. Réessayez pour revenir à votre serveur.",
		"android.browser.errors.rendererStopped": "La page s’est arrêtée de façon inattendue. Réessayez pour rouvrir TwiCC.",
		"android.browser.errors.sessionClosed": "La session du navigateur est fermée.",
		"android.browser.errors.unreachable": "Impossible de joindre {name}. Vérifiez votre connexion et le tunnel HTTPS, puis réessayez.",
		"android.browser.errors.webviewProfiles": "Mettez à jour Android System WebView ou Chrome pour utiliser des instances séparées.",
		"android.browser.menuDescription": "Menu de l’application Android pour {name}",
		"android.browser.version": "TwiCC {version} · {engine}",
		"android.downloads.browserRequired": "Ce téléchargement nécessite un navigateur. Ouvrez la page dans votre navigateur pour enregistrer les exports blob ou data.",
		"android.downloads.errors.failed": "Échec du téléchargement. Vérifiez votre connexion et réessayez.",
		"android.downloads.errors.saveUnavailable": "Ce téléchargement ne peut pas être enregistré. Ouvrez la page dans votre navigateur.",
		"android.downloads.errors.startFailed": "Le téléchargement ne peut pas démarrer. Ouvrez cette page dans votre navigateur.",
		"android.downloads.saved": "{filename} enregistré dans Téléchargements.",
		"android.downloads.saving": "Enregistrement de {filename} dans Téléchargements…",
		"android.downloads.started": "Téléchargement démarré. Consultez la notification de téléchargement Android.",
		"android.files.errors.folderUnsupported": "Le téléversement de dossiers n’est pas disponible. Sélectionnez des fichiers individuels.",
		"android.files.errors.pickerUnavailable": "Aucun sélecteur de fichiers n’est disponible.",
		"android.files.errors.readFailed": "Les fichiers sélectionnés ne peuvent pas être lus. Réessayez.",
		"android.instances.add": "Ajouter une instance",
		"android.instances.addressHint": "HTTPS uniquement. Une nouvelle adresse nécessite une nouvelle connexion.",
		"android.instances.addressLabel": "Adresse du serveur",
		"android.instances.editDescription": "Modifier {name}",
		"android.instances.editTitle": "Modifier l’instance",
		"android.instances.emptyBody": "Ajoutez l’adresse de votre serveur TwiCC pour commencer.",
		"android.instances.emptyTitle": "Aucune instance pour le moment",
		"android.instances.errors.duplicate": "Cette adresse de serveur est déjà enregistrée.",
		"android.instances.errors.httpsRequired": "Utilisez une adresse HTTPS, comme https://twicc.example.com.",
		"android.instances.errors.invalidAddress": "Saisissez une adresse HTTPS valide.",
		"android.instances.errors.invalidPort": "Saisissez un numéro de port valide.",
		"android.instances.errors.originRequired": "Utilisez l’adresse du serveur sans identifiants, requête ni fragment.",
		"android.instances.errors.rootRequired": "Utilisez l’adresse racine du serveur, sans chemin de page.",
		"android.instances.lastUsed": "Dernière utilisation",
		"android.instances.nameLabel": "Nom (facultatif)",
		"android.instances.openDescription": "Ouvrir {name}, {address}",
		"android.instances.remove": "Retirer",
		"android.instances.removeDescription": "Retirer {name}",
		"android.instances.removeMessage": "Retirez cette adresse et demandez la suppression de sa connexion et de son stockage Web dans chaque moteur. Le serveur reste inchangé.",
		"android.instances.removeTitle": "Retirer {name} ?",
		"android.instances.saved": "Instances enregistrées",
		"android.instances.subtitle": "Connectez-vous à votre serveur TwiCC existant via un tunnel HTTPS.",
		"android.instances.title": "TwiCC",
		"android.language.errors.changed": "La langue de l’application a changé. Sélectionnez à nouveau la langue.",
		"android.language.errors.invalidRevision": "La demande de langue de l’application est invalide. Rechargez cette page et réessayez.",
		"android.language.errors.revisionExhausted": "La langue de l’application ne peut pas changer.",
		"android.language.errors.saveFailed": "Impossible d’enregistrer la langue de l’application. Réessayez.",
		"android.language.errors.staleDocument": "Rechargez cette page avant de changer la langue de l’application.",
		"android.language.errors.unsupported": "Cette langue n’est pas disponible dans l’application Android. Mettez à jour l’application et réessayez.",
		"android.language.errors.updateApp": "Mettez à jour l’application Android pour synchroniser la langue de l’interface.",
		"android.language.errors.updateServer": "Mettez à jour ce serveur TwiCC pour synchroniser la langue de l’interface.",
		"android.push.channels.attention": "Questions et approbations",
		"android.push.channels.finished": "Travail terminé",
		"android.push.channels.peer": "Messages et demandes des pairs",
		"android.push.distributorLabel": "Notifications TwiCC",
		"android.push.errors.updateApp": "Les notifications Android ne sont pas disponibles. Mettez à jour l’application Android et réessayez.",
		"android.push.errors.updateServer": "Mettez à jour ce serveur TwiCC pour utiliser des notifications Android détaillées. En attendant, les notifications affichent un message générique.",
		"android.push.extraUsageBody": "{sentence}\n{usage}",
		"android.push.finishedWithShells": "{finished} — {shells}",
		"android.push.genericBody": "Ouvrez TwiCC pour consulter cette notification.",
		"android.push.genericTitle": "Notification TwiCC",
		"android.push.peerBody": "« {title} »\n{preview}",
		"android.push.peerBodyWithProject": "{message}\nProjet : {project}",
		"android.push.peerBodyWithSession": "{message}\nSession : {session}",
		"android.push.peerRequestBody": "{peer} souhaite s’appairer avec votre instance.\nAdresse : {address}",
		"android.push.projectWithParent": "{parent} › {project}",
		"android.push.soundTitle": "TwiCC — {sound}",
		"android.push.titleWithInstance": "{title} · {instance}",
		"android.speech.batteryAdvice": "Vous pouvez écouter la chronologie sans modifier les paramètres de batterie. Android peut retarder ou interrompre la lecture en arrière-plan ou lorsque l’écran est éteint. Autorisez l’utilisation sans restriction de la batterie pour TwiCC afin d’améliorer l’écoute en arrière-plan. Vous pouvez modifier ce réglage dans Paramètres Android → Applications → TwiCC → Batterie.\n\nContinuez sans modification pour commencer, ou ouvrez les paramètres puis réactivez l’écoute.",
		"android.speech.batteryTitle": "Écoute de la chronologie et batterie",
		"android.speech.channel": "Lecture de la chronologie",
		"android.speech.continueWithoutChanges": "Continuer sans modification",
		"android.speech.errors.androidStopped": "Android a arrêté la lecture. Relancez-la.",
		"android.speech.errors.audioEmpty": "L’audio TTS est vide",
		"android.speech.errors.audioFormat": "Format audio de lecture non pris en charge",
		"android.speech.errors.audioRequest": "Échec de la requête TTS ({status})",
		"android.speech.errors.batteryChoiceRequired": "Choisissez comment continuer dans le dialogue de conseil sur la batterie.",
		"android.speech.errors.changeFailed": "Impossible de modifier la lecture. Rechargez la page et réessayez.",
		"android.speech.errors.cookieSupport": "La lecture native nécessite un WebView avec la prise en charge de GET_COOKIE_INFO. Mettez à jour Android System WebView.",
		"android.speech.errors.credentialsUnavailable": "Rouvrez cette instance, puis relancez la lecture.",
		"android.speech.errors.failed": "Échec de la lecture",
		"android.speech.errors.generationFailed": "Échec de la génération de la lecture",
		"android.speech.errors.instanceChanged": "L’instance active a changé.",
		"android.speech.errors.instanceClosed": "L’instance active a été fermée. Relancez la lecture pour continuer.",
		"android.speech.errors.instanceRequired": "Ouvrez une instance enregistrée avant de lancer la lecture.",
		"android.speech.errors.interrupted": "La lecture audio a été interrompue.",
		"android.speech.errors.playbackChanged": "La lecture a changé. Réessayez.",
		"android.speech.errors.playbackFailed": "La lecture audio ne peut pas démarrer.",
		"android.speech.errors.retryPlayback": "Impossible de joindre TwiCC. Relancez la lecture.",
		"android.speech.errors.staleDocument": "Rechargez cette page, puis réessayez la lecture.",
		"android.speech.errors.staleSession": "Cette notification appartient à une ancienne session de lecture.",
		"android.speech.errors.stopping": "La lecture s’arrête. Attendez un instant, puis réessayez.",
		"android.speech.errors.updateRequired": "La lecture n’est pas disponible. Mettez à jour l’application Android et réessayez.",
		"android.speech.errors.visibleRequired": "Ouvrez TwiCC avant de lancer la lecture.",
		"android.speech.notificationTitle": "{name} · Lecture",
		"android.speech.notificationsRequired": "Activez les notifications de lecture pour utiliser Passer et Tout arrêter en arrière-plan. Relancez ensuite la lecture.",
		"android.speech.openSettings": "Ouvrir les paramètres",
		"android.speech.pauseTimeline": "Pause",
		"android.speech.preparing": "Préparation",
		"android.speech.reading": "Lecture",
		"android.speech.reconnecting": "Reconnexion",
		"android.speech.recoveryAnnouncement": "Connexion rétablie. Les messages reçus pendant la coupure ne seront pas lus. La lecture automatique reprend.",
		"android.speech.resumeTimeline": "Reprendre",
		"android.speech.settingsTitle": "Paramètres de lecture requis",
		"android.speech.skip": "Passer",
		"android.speech.stopAll": "Arrêter",
		"android.speech.stopping": "Arrêt",
		"android.speech.timelinePaused": "Lecture de la chronologie en pause",
		"android.speech.waiting": "En attente",
		"backend.tts.adaptationStopped": "L'adaptation du texte s'est arrêtée avant la fin",
		"backend.tts.apiKeyRequired": "TWICC_OPENROUTER_API_KEY n'est pas définie. Ajoutez-la à {path} et redémarrez TwiCC.",
		"backend.tts.audioCut": "L'audio s'est arrêté avant la fin",
		"backend.tts.audioExpired": "L'audio a expiré avant la lecture",
		"backend.tts.audioGone": "L'audio n'est plus disponible",
		"backend.tts.authenticationExpired": "L'authentification a expiré. Ouvrez TwiCC et reconnectez-vous.",
		"backend.tts.catchupIncomplete": "Certains messages n'ont pas pu être chargés. Réactivez la lecture de la chronologie.",
		"backend.tts.chooseMp3": "La sortie du script ressemble à du MP3\xA0: choisissez MP3",
		"backend.tts.chooseWav": "La sortie du script ressemble à du WAV\xA0: choisissez WAV",
		"backend.tts.commandMissing": "Cette configuration de script n'a pas de commande",
		"backend.tts.configMatches": "'config_id' doit être égal à 'script_id'",
		"backend.tts.configNotSaved": "Cette configuration n'est pas encore enregistrée\xA0: réessayez",
		"backend.tts.configNotValidated": "Cette configuration de script n'est pas validée\xA0: testez-la de nouveau dans les paramètres",
		"backend.tts.configurationUnavailable": "La synthèse vocale est indisponible. Vérifiez la configuration active.",
		"backend.tts.failed": "Échec de la synthèse vocale",
		"backend.tts.fieldString": "'{field}' doit être une chaîne de caractères",
		"backend.tts.flacUnsupported": "La sortie du script ressemble à de l'audio FLAC, que TwiCC ne lit pas",
		"backend.tts.formatChoices": "'response_format' doit être l'une des valeurs suivantes\xA0: {choices}",
		"backend.tts.invalidCommand": "La lecture vocale n'a pas pu démarrer. Rechargez la page et réessayez.",
		"backend.tts.modelRequired": "'model' doit être une chaîne non vide",
		"backend.tts.mp4Unsupported": "La sortie du script ressemble à de l'audio MP4, que TwiCC ne lit pas",
		"backend.tts.noAudio": "Le script n'a produit aucun audio",
		"backend.tts.notMp3": "La sortie du script n'est pas du MP3",
		"backend.tts.notWav": "La sortie du script n'est pas du WAV",
		"backend.tts.nothingToRead": "Il n'y a rien à lire à voix haute dans ce texte",
		"backend.tts.oggUnsupported": "La sortie du script ressemble à de l'audio Ogg, que TwiCC ne lit pas",
		"backend.tts.openrouterStatus": "OpenRouter a renvoyé HTTP {status}",
		"backend.tts.openrouterTimeout": "OpenRouter n'a pas répondu à temps",
		"backend.tts.openrouterUnreachable": "Impossible de joindre OpenRouter",
		"backend.tts.originChoices": "'origin' doit être l'une des valeurs suivantes\xA0: {choices}",
		"backend.tts.partFormatChanged": "Le format audio a changé entre deux parties du texte",
		"backend.tts.pcmRateRequired": "Cette configuration de script nécessite une fréquence d'échantillonnage pour le PCM",
		"backend.tts.provider400Hint": "Le fournisseur a renvoyé 400. Vérifiez le modèle, la voix, le format et la longueur du texte.",
		"backend.tts.scopeUnavailable": "La lecture de la chronologie s'est arrêtée. Choisissez une autre chronologie.",
		"backend.tts.scriptAudioLarge": "L'audio du script est trop volumineux",
		"backend.tts.scriptExit": "Le script a échoué (code de sortie {status})",
		"backend.tts.scriptId": "'script_id' doit être une chaîne non vide",
		"backend.tts.scriptMissing": "Cette configuration de script n'existe pas",
		"backend.tts.scriptOutputPath": "La sortie du script n'est pas un unique chemin absolu",
		"backend.tts.scriptPathReused": "Le script a renvoyé deux fois le même chemin",
		"backend.tts.scriptSignal": "Le script a été arrêté par le signal {signal}",
		"backend.tts.scriptStalled": "Le script a cessé d'envoyer de l'audio",
		"backend.tts.scriptStart": "Le script n'a pas pu démarrer",
		"backend.tts.scriptUnreadable": "Le fichier du script est illisible",
		"backend.tts.sessionLost": "La lecture vocale s'est arrêtée. Relancez la lecture ou réactivez la lecture de la chronologie.",
		"backend.tts.speedNumber": "'speed' doit être un nombre fini strictement positif",
		"backend.tts.stopped": "Synthèse vocale arrêtée",
		"backend.tts.summaryChanged": "Le résumé enregistré a changé. Actualisez-le avant la lecture.",
		"backend.tts.summaryFingerprint": "'summary_result_hash' doit être l'empreinte du résultat enregistré",
		"backend.tts.summaryGone": "Le résumé n'est plus disponible",
		"backend.tts.summaryHandle": "'summary_id' doit être un handle non vide",
		"backend.tts.summaryHashRequiresSaved": "'summary_result_hash' nécessite 'saved_summary_id'",
		"backend.tts.summaryReference": "Un résumé nécessite exactement une référence, sans texte ni test de configuration",
		"backend.tts.summarySavedGone": "Le résumé enregistré n'est plus disponible",
		"backend.tts.summarySavedId": "'saved_summary_id' doit être un entier positif",
		"backend.tts.summaryStopped": "Le résumé s'est arrêté avant la fin",
		"backend.tts.textRequired": "'text' doit être une chaîne non vide",
		"backend.tts.textTooLong": "Le texte est trop long pour être envoyé",
		"backend.tts.wav16bit": "Le WAV du script n'est pas du PCM 16 bits",
		"backend.tts.wavFormat": "Le WAV du script a une fréquence d'échantillonnage ou un nombre de canaux non pris en charge",
		"common.actions.cancel": "Annuler",
		"common.actions.ok": "OK",
		"common.actions.retry": "Réessayer",
		"common.actions.save": "Enregistrer",
		"common.android.errors.actionFailed": "Impossible de terminer cette action. Rechargez cette page et réessayez.",
		"common.android.errors.closed": "La connexion à l'application Android s'est fermée. Rechargez cette page et réessayez.",
		"common.android.errors.invalidNotification": "Cible de notification invalide.",
		"common.android.errors.speechPreparing": "La synthèse vocale se prépare. Réessayez dans un instant.",
		"common.android.errors.tooLarge": "Cette action contient trop de données. Essayez avec une sélection plus petite.",
		"common.android.errors.unreachable": "Impossible de contacter l'application Android. Rechargez cette page et réessayez.",
		"common.labels.unknown": "Inconnu",
		"common.sessionCount": "{count} session | {count} sessions",
		"composer.status.startingProvider": "Démarrage du processus {provider}...",
		"navigation.android.active": "Active",
		"navigation.android.browser": "Ouvrir dans le navigateur",
		"navigation.android.forceRefresh": "Recharger la page (sans cache)",
		"navigation.android.manage": "Gérer les instances",
		"navigation.android.menu": "Menu de l'application Android",
		"navigation.android.refresh": "Actualiser",
		"navigation.android.refreshHint": "Recharge cette page en ignorant son cache de ressources. Votre connexion et vos préférences restent enregistrées. Le texte non enregistré peut être perdu.",
		"navigation.android.refreshQuestion": "Forcer l'actualisation\xA0?",
		"navigation.android.reload": "Recharger la page",
		"navigation.android.serverHome": "Accueil du serveur",
		"navigation.android.unsupportedState": "État de l'application Android non pris en charge.",
		"peer.toast.unnamedInstance": "Une instance",
		"session.notifications.activityTimeout": "{provider} arrêté\xA0: aucune activité depuis 3 heures",
		"session.notifications.agentStoppedBeforeAck": "L'agent s'est arrêté avant d'avoir confirmé la réception de votre message.",
		"session.notifications.approval": "🖐️ {provider} a besoin de votre approbation",
		"session.notifications.attention": "{provider} a besoin de votre attention",
		"session.notifications.body": "Projet\xA0: {project}\nSession\xA0: {session}",
		"session.notifications.creditsRemaining": "{count} crédit restant. | {count} crédits restants.",
		"session.notifications.creditsUsed": "{used} crédit utilisé. | {used} crédits utilisés.",
		"session.notifications.creditsUsedOf": "{used} crédit utilisé sur {limit}. | {used} crédits utilisés sur {limit}.",
		"session.notifications.enabledLabel": "Notifications activées",
		"session.notifications.ephemeral": "Session éphémère terminée",
		"session.notifications.ephemeralSession": "Session éphémère",
		"session.notifications.errors.update": "Impossible de mettre à jour les notifications de la session",
		"session.notifications.extraUsageSentence": "{provider} utilise actuellement vos crédits d'utilisation supplémentaire, facturés en plus de votre forfait.",
		"session.notifications.extraUsageTitle": "{provider} — Utilisation supplémentaire démarrée",
		"session.notifications.finished": "{provider} a terminé son travail",
		"session.notifications.instance": "une instance",
		"session.notifications.invalidTitle": "Titre invalide",
		"session.notifications.invalidTitleDetail": "Titre de session invalide\xA0: {error}",
		"session.notifications.invalidTitleUnknown": "Titre de session invalide\xA0: erreur inconnue",
		"session.notifications.moneyUsed": "{used} {currency} utilisés.",
		"session.notifications.moneyUsedOf": "{used} {currency} utilisés sur {limit}.",
		"session.notifications.mute": "Mettre en sourdine",
		"session.notifications.muteTooltip": "Notifications activées — cliquez pour désactiver la notification «\xA0terminé\xA0» et ne plus afficher cette session comme non lue",
		"session.notifications.mutedLabel": "En sourdine",
		"session.notifications.notConnected": "Non connecté",
		"session.notifications.peerAccepted": "«\xA0{peer}\xA0» a accepté votre demande de pair",
		"session.notifications.peerDelivered": "Votre message{title} à «\xA0{peer}\xA0» a été transmis",
		"session.notifications.peerDone": "«\xA0{peer}\xA0» a marqué votre message{title} comme traité",
		"session.notifications.peerMessage": "Message de {peer}",
		"session.notifications.peerRefused": "Votre message{title} à «\xA0{peer}\xA0» a été refusé",
		"session.notifications.peerReply": "Réponse de {peer}",
		"session.notifications.peerRequest": "Demande de pair",
		"session.notifications.peerRequestFrom": "Demande de pair de {peer}",
		"session.notifications.peerVerification": "«\xA0{peer}\xA0» a accepté votre demande — terminez la vérification du code pour l'activer",
		"session.notifications.providerDisabled": "Impossible d'envoyer à {provider}\xA0: il est désactivé. Activez-le dans Paramètres → Fournisseurs.",
		"session.notifications.question": "🖐️ {provider} a une question pour vous",
		"session.notifications.settingsInvalid": "Une valeur de paramètre est invalide.",
		"session.notifications.shells": "{count} shell en arrière-plan toujours en cours | {count} shells en arrière-plan toujours en cours",
		"session.notifications.startTimeout": "{provider} arrêté\xA0: démarrage impossible en 1 minute",
		"session.notifications.terminated": "{provider} s'est arrêté à cause d'une erreur",
		"session.notifications.turnFinished": "{provider} a terminé son tour",
		"session.notifications.unknownError": "Erreur inconnue",
		"session.notifications.unmuteTooltip": "En sourdine — cliquez pour rétablir la notification «\xA0terminé\xA0» et l'indicateur de non-lu",
		"session.notifications.updateAvailable": "TwiCC v{version} est disponible",
		"session.notifications.viewChanges": "Voir les modifications",
		"session.speech.errors.cut": "L'audio s'est arrêté avant la fin",
		"session.speech.errors.gone": "L'audio n'est plus disponible",
		"session.speech.errors.identityRequired": "Une identité de résumé est obligatoire",
		"session.speech.errors.initializing": "La synthèse vocale native s'initialise. Réessayez.",
		"session.speech.errors.nativeUnavailable": "La synthèse vocale native est indisponible. Rouvrez cette page.",
		"session.speech.errors.noConfig": "Aucune configuration de synthèse vocale active",
		"session.speech.errors.refused": "Le navigateur a refusé de lire l'audio",
		"session.speech.errors.requestFailed": "Échec de la requête ({status})",
		"session.speech.errors.startFailed": "La synthèse vocale n'a pas pu démarrer. Rechargez la page et réessayez.",
		"session.speech.errors.summaryGone": "Le résumé n'est plus disponible",
		"session.speech.errors.unavailable": "La synthèse vocale est indisponible. Rechargez la page et réessayez.",
		"session.speech.errors.unreachable": "Impossible de joindre TwiCC",
		"settings.general.language.label": "Langue",
		"settings.notifications.sounds.beep": "Beep",
		"settings.notifications.sounds.bloop": "Bloop",
		"settings.notifications.sounds.chime": "Carillon",
		"settings.notifications.sounds.knock": "Toc-toc",
		"settings.notifications.sounds.ping": "Ping",
		"settings.notifications.sounds.pop": "Pop",
		"settings.notifications.sounds.ripple": "Ondulation",
		"settings.notifications.sounds.signal": "Signal",
		"settings.notifications.sounds.sweep": "Balayage",
		"settings.notifications.sounds.tink": "Tintement",
		"settings.notifications.targets.extraUsage": "Utilisation supplémentaire",
		"workspace.projectCount": "{count} projet | {count} projets"
	};
})), eg = /* @__PURE__ */ c({ default: () => Qh }), tg = o((() => {
	$h();
})), ng = /* @__PURE__ */ s(((e, t) => {
	(function(n, r) {
		typeof e == "object" && t !== void 0 ? t.exports = r(Ch()) : typeof define == "function" && define.amd ? define(["dayjs"], r) : (n = typeof globalThis < "u" ? globalThis : n || self).dayjs_locale_fr = r(n.dayjs);
	})(e, (function(e) {
		function t(e) {
			return e && typeof e == "object" && "default" in e ? e : { default: e };
		}
		var n = t(e), r = {
			name: "fr",
			weekdays: "dimanche_lundi_mardi_mercredi_jeudi_vendredi_samedi".split("_"),
			weekdaysShort: "dim._lun._mar._mer._jeu._ven._sam.".split("_"),
			weekdaysMin: "di_lu_ma_me_je_ve_sa".split("_"),
			months: "janvier_février_mars_avril_mai_juin_juillet_août_septembre_octobre_novembre_décembre".split("_"),
			monthsShort: "janv._févr._mars_avr._mai_juin_juil._août_sept._oct._nov._déc.".split("_"),
			weekStart: 1,
			yearStart: 4,
			formats: {
				LT: "HH:mm",
				LTS: "HH:mm:ss",
				L: "DD/MM/YYYY",
				LL: "D MMMM YYYY",
				LLL: "D MMMM YYYY HH:mm",
				LLLL: "dddd D MMMM YYYY HH:mm"
			},
			relativeTime: {
				future: "dans %s",
				past: "il y a %s",
				s: "quelques secondes",
				m: "une minute",
				mm: "%d minutes",
				h: "une heure",
				hh: "%d heures",
				d: "un jour",
				dd: "%d jours",
				M: "un mois",
				MM: "%d mois",
				y: "un an",
				yy: "%d ans"
			},
			ordinal: function(e) {
				return "" + e + (e === 1 ? "er" : "");
			}
		};
		return n.default.locale(r, null, !0), r;
	}));
})), rg = /* @__PURE__ */ c({ default: () => ag }), ig, ag, og = o((() => {
	Lh(), Ih(), ig = {
		$code: "fr",
		$name: "Français",
		$dir: "ltr",
		carousel: "Carrousel",
		clearEntry: "Effacer l'entrée",
		close: "Fermer",
		copied: "Copié",
		copy: "Copier",
		currentValue: "Valeur actuelle",
		decrement: "Diminuer",
		dropFileHere: "Drop file here or click to browse",
		dropFilesHere: "Drop files here or click to browse",
		error: "Erreur",
		goToSlide: (e, t) => `Aller \xE0 la diapositive ${e} de ${t}`,
		hidePassword: "Masquer le mot de passe",
		increment: "Augmenter",
		loading: "Chargement",
		nextSlide: "Diapositive suivante",
		numOptionsSelected: (e) => e === 0 ? "Aucune option sélectionnée" : e === 1 ? "1 option sélectionnée" : `${e} options s\xE9lectionn\xE9es`,
		pauseAnimation: "Suspendre l'animation",
		playAnimation: "Lire l'animation",
		previousSlide: "Diapositive précédente",
		progress: "Progrès",
		remove: "Retirer",
		resize: "Redimensionner",
		scrollableRegion: "Région défilante",
		scrollToEnd: "Faire défiler jusqu'à la fin",
		scrollToStart: "Faire défiler jusqu'au début",
		selectAColorFromTheScreen: "Sélectionnez une couleur à l'écran",
		showPassword: "Montrer le mot de passe",
		slideNum: (e) => `Diapositive ${e}`,
		toggleColorFormat: "Changer le format de couleur",
		zoomIn: "Zoomer",
		zoomOut: "Dézoomer"
	}, Th(ig), ag = ig;
})), sg, cg = o((() => {
	sg = {
		"android.browser.errors.applicationUnavailable": "Nenhum aplicativo pode abrir este link.",
		"android.browser.errors.browserUnavailable": "Nenhum navegador está disponível para abrir este link.",
		"android.browser.errors.certificate": "Não foi possível verificar o certificado HTTPS. Verifique o certificado do túnel e a data do seu telefone.",
		"android.browser.errors.engineStart": "{engine} não pôde iniciar. Verifique a instalação do mecanismo e reabra o TwiCC.",
		"android.browser.errors.httpStatus": "O servidor retornou HTTP {status}. Verifique seu túnel ou servidor e tente novamente.",
		"android.browser.errors.instanceRemoved": "Esta instância não está mais salva.",
		"android.browser.errors.linkUnsupported": "Este tipo de link não é compatível.",
		"android.browser.errors.pageBlocked": "Esta página não pode abrir no TwiCC. Tente novamente para voltar ao seu servidor.",
		"android.browser.errors.rendererStopped": "A página parou inesperadamente. Tente novamente para reabrir o TwiCC.",
		"android.browser.errors.sessionClosed": "A sessão do navegador está fechada.",
		"android.browser.errors.unreachable": "Não é possível acessar {name}. Verifique sua conexão e o túnel HTTPS e tente novamente.",
		"android.browser.errors.webviewProfiles": "Atualize o Android System WebView ou o Chrome para usar instâncias separadas.",
		"android.browser.menuDescription": "Menu do aplicativo Android para {name}",
		"android.browser.version": "TwiCC {version} · {engine}",
		"android.downloads.browserRequired": "Este download precisa de um navegador. Abra a página no navegador para salvar exportações blob ou data.",
		"android.downloads.errors.failed": "O download falhou. Verifique sua conexão e tente novamente.",
		"android.downloads.errors.saveUnavailable": "Não é possível salvar este download. Tente abrir a página no navegador.",
		"android.downloads.errors.startFailed": "O download não pôde iniciar. Tente abrir esta página no navegador.",
		"android.downloads.saved": "{filename} salvo em Downloads.",
		"android.downloads.saving": "Salvando {filename} em Downloads…",
		"android.downloads.started": "Download iniciado. Verifique a notificação de downloads do Android.",
		"android.files.errors.folderUnsupported": "O upload de pastas não está disponível. Selecione arquivos individuais.",
		"android.files.errors.pickerUnavailable": "Nenhum seletor de arquivos está disponível.",
		"android.files.errors.readFailed": "Não foi possível ler os arquivos selecionados. Tente novamente.",
		"android.instances.add": "Adicionar instância",
		"android.instances.addressHint": "Somente HTTPS. Um novo endereço exige um novo login.",
		"android.instances.addressLabel": "Endereço do servidor",
		"android.instances.editDescription": "Editar {name}",
		"android.instances.editTitle": "Editar instância",
		"android.instances.emptyBody": "Adicione o endereço do seu servidor TwiCC para começar.",
		"android.instances.emptyTitle": "Nenhuma instância ainda",
		"android.instances.errors.duplicate": "Este endereço de servidor já está salvo.",
		"android.instances.errors.httpsRequired": "Use um endereço HTTPS, como https://twicc.example.com.",
		"android.instances.errors.invalidAddress": "Digite um endereço HTTPS válido.",
		"android.instances.errors.invalidPort": "Digite um número de porta válido.",
		"android.instances.errors.originRequired": "Use o endereço do servidor sem credenciais, consulta ou fragmento.",
		"android.instances.errors.rootRequired": "Use o endereço raiz do servidor, sem caminho de página.",
		"android.instances.lastUsed": "Última usada",
		"android.instances.nameLabel": "Nome (opcional)",
		"android.instances.openDescription": "Abrir {name}, {address}",
		"android.instances.remove": "Remover",
		"android.instances.removeDescription": "Remover {name}",
		"android.instances.removeMessage": "Remova este endereço e solicite a exclusão do login e do armazenamento Web em cada mecanismo. O servidor permanece inalterado.",
		"android.instances.removeTitle": "Remover {name}?",
		"android.instances.saved": "Instâncias salvas",
		"android.instances.subtitle": "Conecte-se ao seu servidor TwiCC existente por um túnel HTTPS.",
		"android.instances.title": "TwiCC",
		"android.language.errors.changed": "O idioma do aplicativo mudou. Selecione o idioma novamente.",
		"android.language.errors.invalidRevision": "A solicitação de idioma do aplicativo é inválida. Recarregue esta página e tente novamente.",
		"android.language.errors.revisionExhausted": "O idioma do aplicativo não pode ser alterado.",
		"android.language.errors.saveFailed": "Não foi possível salvar o idioma do aplicativo. Tente novamente.",
		"android.language.errors.staleDocument": "Recarregue esta página antes de alterar o idioma do aplicativo.",
		"android.language.errors.unsupported": "Este idioma não está disponível no aplicativo Android. Atualize o aplicativo e tente novamente.",
		"android.language.errors.updateApp": "Atualize o aplicativo Android para sincronizar o idioma da interface.",
		"android.language.errors.updateServer": "Atualize este servidor TwiCC para sincronizar o idioma da interface.",
		"android.push.channels.attention": "Perguntas e aprovações",
		"android.push.channels.finished": "Trabalho concluído",
		"android.push.channels.peer": "Mensagens e solicitações de instâncias parceiras",
		"android.push.distributorLabel": "Notificações do TwiCC",
		"android.push.errors.updateApp": "As notificações do Android não estão disponíveis. Atualize o aplicativo Android e tente novamente.",
		"android.push.errors.updateServer": "Atualize este servidor TwiCC para usar notificações detalhadas do Android. Até lá, as notificações mostram uma mensagem genérica.",
		"android.push.extraUsageBody": "{sentence}\n{usage}",
		"android.push.finishedWithShells": "{finished} — {shells}",
		"android.push.genericBody": "Abra o TwiCC para ver esta notificação.",
		"android.push.genericTitle": "Notificação do TwiCC",
		"android.push.peerBody": "“{title}”\n{preview}",
		"android.push.peerBodyWithProject": "{message}\nProjeto: {project}",
		"android.push.peerBodyWithSession": "{message}\nSessão: {session}",
		"android.push.peerRequestBody": "{peer} quer parear com sua instância.\nEndereço: {address}",
		"android.push.projectWithParent": "{parent} › {project}",
		"android.push.soundTitle": "TwiCC — {sound}",
		"android.push.titleWithInstance": "{title} · {instance}",
		"android.speech.batteryAdvice": "Você pode ouvir a linha do tempo sem alterar as configurações de bateria. O Android pode atrasar ou interromper a leitura em segundo plano ou com a tela apagada. Permita o uso irrestrito de bateria para o TwiCC para melhorar a escuta em segundo plano. Você pode alterar isso depois em Configurações do Android → Aplicativos → TwiCC → Bateria.\n\nContinue sem alterações para começar agora ou abra as configurações e ative a escuta novamente.",
		"android.speech.batteryTitle": "Escuta da linha do tempo e bateria",
		"android.speech.channel": "Leitura da linha do tempo",
		"android.speech.continueWithoutChanges": "Continuar sem alterações",
		"android.speech.errors.androidStopped": "O Android parou a leitura. Inicie-a novamente.",
		"android.speech.errors.audioEmpty": "O áudio TTS está vazio",
		"android.speech.errors.audioFormat": "Formato de áudio de leitura não compatível",
		"android.speech.errors.audioRequest": "A solicitação TTS falhou ({status})",
		"android.speech.errors.batteryChoiceRequired": "Escolha como continuar no diálogo de orientação sobre bateria.",
		"android.speech.errors.changeFailed": "Não foi possível alterar a reprodução da leitura. Recarregue a página e tente novamente.",
		"android.speech.errors.cookieSupport": "A leitura nativa exige um WebView com suporte a GET_COOKIE_INFO. Atualize o Android System WebView.",
		"android.speech.errors.credentialsUnavailable": "Reabra esta instância e inicie a leitura novamente.",
		"android.speech.errors.failed": "A leitura falhou",
		"android.speech.errors.generationFailed": "A geração da leitura falhou",
		"android.speech.errors.instanceChanged": "A instância ativa mudou.",
		"android.speech.errors.instanceClosed": "A instância ativa foi fechada. Inicie a leitura novamente para continuar.",
		"android.speech.errors.instanceRequired": "Abra uma instância salva antes de iniciar a leitura.",
		"android.speech.errors.interrupted": "A reprodução de áudio foi interrompida.",
		"android.speech.errors.playbackChanged": "A reprodução da leitura mudou. Tente novamente.",
		"android.speech.errors.playbackFailed": "A reprodução de áudio não pôde iniciar.",
		"android.speech.errors.retryPlayback": "Não foi possível acessar o TwiCC. Inicie a reprodução novamente.",
		"android.speech.errors.staleDocument": "Recarregue esta página e tente a leitura novamente.",
		"android.speech.errors.staleSession": "Esta notificação pertence a uma sessão de leitura anterior.",
		"android.speech.errors.stopping": "A leitura está parando. Aguarde um momento e tente novamente.",
		"android.speech.errors.updateRequired": "A leitura não está disponível. Atualize o aplicativo Android e tente novamente.",
		"android.speech.errors.visibleRequired": "Abra o TwiCC antes de iniciar a leitura.",
		"android.speech.notificationTitle": "{name} · Leitura",
		"android.speech.notificationsRequired": "Ative as notificações de leitura para usar Pular e Parar tudo em segundo plano. Depois, inicie a leitura novamente.",
		"android.speech.openSettings": "Abrir Configurações",
		"android.speech.pauseTimeline": "Pausar",
		"android.speech.preparing": "Preparando",
		"android.speech.reading": "Lendo",
		"android.speech.reconnecting": "Reconectando",
		"android.speech.recoveryAnnouncement": "Conexão restabelecida. As mensagens recebidas durante a interrupção não serão lidas. A leitura automática será retomada.",
		"android.speech.resumeTimeline": "Retomar",
		"android.speech.settingsTitle": "Configurações de leitura necessárias",
		"android.speech.skip": "Pular",
		"android.speech.stopAll": "Parar",
		"android.speech.stopping": "Parando",
		"android.speech.timelinePaused": "Leitura da linha do tempo pausada",
		"android.speech.waiting": "Aguardando",
		"backend.tts.adaptationStopped": "A adaptação do texto parou antes do fim",
		"backend.tts.apiKeyRequired": "TWICC_OPENROUTER_API_KEY não está definida. Adicione-a a {path} e reinicie o TwiCC.",
		"backend.tts.audioCut": "O áudio parou antes do fim",
		"backend.tts.audioExpired": "O áudio expirou antes da reprodução",
		"backend.tts.audioGone": "O áudio não está mais disponível",
		"backend.tts.authenticationExpired": "A autenticação expirou. Abra o TwiCC e entre novamente.",
		"backend.tts.catchupIncomplete": "Algumas mensagens não puderam ser carregadas. Ative a leitura da linha do tempo novamente.",
		"backend.tts.chooseMp3": "A saída do script parece MP3: escolha MP3",
		"backend.tts.chooseWav": "A saída do script parece WAV: escolha WAV",
		"backend.tts.commandMissing": "Esta configuração de script não tem comando",
		"backend.tts.configMatches": "'config_id' deve ser igual a 'script_id'",
		"backend.tts.configNotSaved": "Esta configuração ainda não está salva: tente novamente",
		"backend.tts.configNotValidated": "Esta configuração de script não está validada: teste-a novamente nas configurações",
		"backend.tts.configurationUnavailable": "A leitura em voz alta não está disponível. Verifique a configuração ativa.",
		"backend.tts.failed": "Falha na leitura em voz alta",
		"backend.tts.fieldString": "'{field}' deve ser uma string",
		"backend.tts.flacUnsupported": "A saída do script parece áudio FLAC, que o TwiCC não reproduz",
		"backend.tts.formatChoices": "'response_format' deve ser um dos seguintes: {choices}",
		"backend.tts.invalidCommand": "Não foi possível iniciar a leitura em voz alta. Recarregue a página e tente novamente.",
		"backend.tts.modelRequired": "'model' deve ser uma string não vazia",
		"backend.tts.mp4Unsupported": "A saída do script parece áudio MP4, que o TwiCC não reproduz",
		"backend.tts.noAudio": "O script não forneceu áudio",
		"backend.tts.notMp3": "A saída do script não é MP3",
		"backend.tts.notWav": "A saída do script não é WAV",
		"backend.tts.nothingToRead": "Não há nada para ler em voz alta neste texto",
		"backend.tts.oggUnsupported": "A saída do script parece áudio Ogg, que o TwiCC não reproduz",
		"backend.tts.openrouterStatus": "O OpenRouter retornou HTTP {status}",
		"backend.tts.openrouterTimeout": "O OpenRouter não respondeu a tempo",
		"backend.tts.openrouterUnreachable": "Não foi possível acessar o OpenRouter",
		"backend.tts.originChoices": "'origin' deve ser um dos seguintes: {choices}",
		"backend.tts.partFormatChanged": "O formato de áudio mudou entre duas partes do texto",
		"backend.tts.pcmRateRequired": "Esta configuração de script requer uma taxa de amostragem para PCM",
		"backend.tts.provider400Hint": "O provedor retornou 400. Verifique o modelo, a voz, o formato e o tamanho do texto.",
		"backend.tts.scopeUnavailable": "A leitura da linha do tempo parou. Escolha outra linha do tempo.",
		"backend.tts.scriptAudioLarge": "O áudio do script é muito grande",
		"backend.tts.scriptExit": "O script falhou (status de saída {status})",
		"backend.tts.scriptId": "'script_id' deve ser uma string não vazia",
		"backend.tts.scriptMissing": "Esta configuração de script não existe",
		"backend.tts.scriptOutputPath": "A saída do script não é um caminho absoluto único",
		"backend.tts.scriptPathReused": "O script retornou o mesmo caminho duas vezes",
		"backend.tts.scriptSignal": "O script foi parado pelo sinal {signal}",
		"backend.tts.scriptStalled": "O script parou de enviar áudio",
		"backend.tts.scriptStart": "O script não pôde iniciar",
		"backend.tts.scriptUnreadable": "Não é possível ler o arquivo de áudio retornado pelo script",
		"backend.tts.sessionLost": "A leitura em voz alta foi parada. Inicie a reprodução ou ative a leitura da linha do tempo novamente.",
		"backend.tts.speedNumber": "'speed' deve ser um número positivo finito",
		"backend.tts.stopped": "Leitura em voz alta parada",
		"backend.tts.summaryChanged": "O resumo salvo mudou. Atualize-o antes de ler.",
		"backend.tts.summaryFingerprint": "'summary_result_hash' deve ser a impressão digital do resultado salvo",
		"backend.tts.summaryGone": "O resumo não está mais disponível",
		"backend.tts.summaryHandle": "'summary_id' deve ser um identificador não vazio",
		"backend.tts.summaryHashRequiresSaved": "'summary_result_hash' requer 'saved_summary_id'",
		"backend.tts.summaryReference": "Um resumo requer exatamente uma referência, sem texto ou teste de configuração",
		"backend.tts.summarySavedGone": "O resumo salvo não está mais disponível",
		"backend.tts.summarySavedId": "'saved_summary_id' deve ser um número inteiro positivo",
		"backend.tts.summaryStopped": "O resumo parou antes do fim",
		"backend.tts.textRequired": "'text' deve ser uma string não vazia",
		"backend.tts.textTooLong": "O texto é muito longo para enviar",
		"backend.tts.wav16bit": "O WAV do script não é PCM de 16 bits",
		"backend.tts.wavFormat": "O WAV do script tem uma taxa de amostragem ou um número de canais incompatível",
		"common.actions.cancel": "Cancelar",
		"common.actions.ok": "OK",
		"common.actions.retry": "Tentar novamente",
		"common.actions.save": "Salvar",
		"common.android.errors.actionFailed": "Não foi possível concluir esta ação. Recarregue esta página e tente novamente.",
		"common.android.errors.closed": "A conexão com o aplicativo Android foi encerrada. Recarregue esta página e tente novamente.",
		"common.android.errors.invalidNotification": "Destino de notificação inválido.",
		"common.android.errors.speechPreparing": "A leitura em voz alta está sendo preparada. Tente novamente em instantes.",
		"common.android.errors.tooLarge": "Esta ação contém dados demais. Tente uma seleção menor.",
		"common.android.errors.unreachable": "Não foi possível contatar o aplicativo Android. Recarregue esta página e tente novamente.",
		"common.labels.unknown": "Desconhecido",
		"common.sessionCount": "{count} sessão | {count} sessões",
		"composer.status.startingProvider": "Iniciando processo do {provider}...",
		"navigation.android.active": "Ativa",
		"navigation.android.browser": "Abrir no navegador",
		"navigation.android.forceRefresh": "Recarregar página (sem cache)",
		"navigation.android.manage": "Gerenciar instâncias",
		"navigation.android.menu": "Menu do aplicativo Android",
		"navigation.android.refresh": "Atualizar",
		"navigation.android.refreshHint": "Recarregue esta página sem usar o cache de recursos. Seu login e suas preferências permanecem salvos. Textos não salvos podem ser perdidos.",
		"navigation.android.refreshQuestion": "Forçar atualização?",
		"navigation.android.reload": "Recarregar página",
		"navigation.android.serverHome": "Página inicial do servidor",
		"navigation.android.unsupportedState": "Estado do aplicativo Android não compatível.",
		"peer.toast.unnamedInstance": "Uma instância",
		"session.notifications.activityTimeout": "{provider} parou: sem atividade por 3 horas",
		"session.notifications.agentStoppedBeforeAck": "O agente parou antes de confirmar o recebimento da sua mensagem.",
		"session.notifications.approval": "🖐️ {provider} precisa da sua aprovação",
		"session.notifications.attention": "{provider} precisa da sua atenção",
		"session.notifications.body": "Projeto: {project}\nSessão: {session}",
		"session.notifications.creditsRemaining": "{count} crédito restante. | {count} créditos restantes.",
		"session.notifications.creditsUsed": "{used} crédito usado. | {used} créditos usados.",
		"session.notifications.creditsUsedOf": "{used} de {limit} crédito usado. | {used} de {limit} créditos usados.",
		"session.notifications.enabledLabel": "Notificações ativadas",
		"session.notifications.ephemeral": "Sessão efêmera concluída",
		"session.notifications.ephemeralSession": "Sessão efêmera",
		"session.notifications.errors.update": "Falha ao atualizar notificações da sessão",
		"session.notifications.extraUsageSentence": "{provider} está consumindo seus créditos de uso extra, cobrados além do seu plano.",
		"session.notifications.extraUsageTitle": "{provider} — Uso extra iniciado",
		"session.notifications.finished": "{provider} concluiu o trabalho",
		"session.notifications.instance": "uma instância",
		"session.notifications.invalidTitle": "Título inválido",
		"session.notifications.invalidTitleDetail": "Título de sessão inválido: {error}",
		"session.notifications.invalidTitleUnknown": "Título de sessão inválido: Erro desconhecido",
		"session.notifications.moneyUsed": "{used} {currency} usados.",
		"session.notifications.moneyUsedOf": "{used} de {limit} {currency} usados.",
		"session.notifications.mute": "Silenciar",
		"session.notifications.muteTooltip": "Notificações ativadas — clique para silenciar a notificação de conclusão e deixar de marcar esta sessão como não lida",
		"session.notifications.mutedLabel": "Silenciada",
		"session.notifications.notConnected": "Sem conexão",
		"session.notifications.peerAccepted": "\"{peer}\" aceitou sua solicitação de pareamento",
		"session.notifications.peerDelivered": "Sua mensagem{title} para \"{peer}\" foi entregue",
		"session.notifications.peerDone": "\"{peer}\" marcou sua mensagem{title} como concluída",
		"session.notifications.peerMessage": "Mensagem de {peer}",
		"session.notifications.peerRefused": "Sua mensagem{title} para \"{peer}\" foi recusada",
		"session.notifications.peerReply": "Resposta de {peer}",
		"session.notifications.peerRequest": "Solicitação de pareamento",
		"session.notifications.peerRequestFrom": "Solicitação de pareamento de {peer}",
		"session.notifications.peerVerification": "\"{peer}\" aceitou sua solicitação — conclua a verificação do código para ativar",
		"session.notifications.providerDisabled": "Não é possível enviar para {provider}: ele está desativado. Ative-o em Configurações → Provedores.",
		"session.notifications.question": "🖐️ {provider} tem uma pergunta para você",
		"session.notifications.settingsInvalid": "Um valor das configurações é inválido.",
		"session.notifications.shells": "{count} shell em segundo plano ainda em execução | {count} shells em segundo plano ainda em execução",
		"session.notifications.startTimeout": "{provider} parou: não iniciou em 1 minuto",
		"session.notifications.terminated": "{provider} encerrou devido a um erro",
		"session.notifications.turnFinished": "{provider} concluiu o turno",
		"session.notifications.unknownError": "Erro desconhecido",
		"session.notifications.unmuteTooltip": "Silenciada — clique para restaurar a notificação de conclusão e a marcação de não lida",
		"session.notifications.updateAvailable": "TwiCC v{version} está disponível",
		"session.notifications.viewChanges": "Ver alterações",
		"session.speech.errors.cut": "O áudio parou antes do fim",
		"session.speech.errors.gone": "O áudio não está mais disponível",
		"session.speech.errors.identityRequired": "É necessária uma identificação do resumo",
		"session.speech.errors.initializing": "A leitura em voz alta nativa está sendo iniciada. Tente novamente.",
		"session.speech.errors.nativeUnavailable": "A leitura em voz alta nativa está indisponível. Abra esta página novamente.",
		"session.speech.errors.noConfig": "Nenhuma configuração de leitura em voz alta está ativa",
		"session.speech.errors.refused": "O navegador recusou a reprodução do áudio",
		"session.speech.errors.requestFailed": "A solicitação falhou ({status})",
		"session.speech.errors.startFailed": "Não foi possível iniciar a leitura em voz alta. Recarregue a página e tente novamente.",
		"session.speech.errors.summaryGone": "O resumo não está mais disponível",
		"session.speech.errors.unavailable": "A leitura em voz alta está indisponível. Recarregue a página e tente novamente.",
		"session.speech.errors.unreachable": "Não foi possível contatar o TwiCC",
		"settings.general.language.label": "Idioma",
		"settings.notifications.sounds.beep": "Bipe",
		"settings.notifications.sounds.bloop": "Bolha",
		"settings.notifications.sounds.chime": "Sino",
		"settings.notifications.sounds.knock": "Batida",
		"settings.notifications.sounds.ping": "Ping",
		"settings.notifications.sounds.pop": "Estalo",
		"settings.notifications.sounds.ripple": "Ondulação",
		"settings.notifications.sounds.signal": "Sinal",
		"settings.notifications.sounds.sweep": "Varredura",
		"settings.notifications.sounds.tink": "Tilintar",
		"settings.notifications.targets.extraUsage": "Uso extra",
		"workspace.projectCount": "{count} projeto | {count} projetos"
	};
})), lg = /* @__PURE__ */ c({ default: () => sg }), ug = o((() => {
	cg();
})), dg = /* @__PURE__ */ s(((e, t) => {
	(function(n, r) {
		typeof e == "object" && t !== void 0 ? t.exports = r(Ch()) : typeof define == "function" && define.amd ? define(["dayjs"], r) : (n = typeof globalThis < "u" ? globalThis : n || self).dayjs_locale_pt_br = r(n.dayjs);
	})(e, (function(e) {
		function t(e) {
			return e && typeof e == "object" && "default" in e ? e : { default: e };
		}
		var n = t(e), r = {
			name: "pt-br",
			weekdays: "domingo_segunda-feira_terça-feira_quarta-feira_quinta-feira_sexta-feira_sábado".split("_"),
			weekdaysShort: "dom_seg_ter_qua_qui_sex_sáb".split("_"),
			weekdaysMin: "Do_2ª_3ª_4ª_5ª_6ª_Sá".split("_"),
			months: "janeiro_fevereiro_março_abril_maio_junho_julho_agosto_setembro_outubro_novembro_dezembro".split("_"),
			monthsShort: "jan_fev_mar_abr_mai_jun_jul_ago_set_out_nov_dez".split("_"),
			ordinal: function(e) {
				return e + "º";
			},
			formats: {
				LT: "HH:mm",
				LTS: "HH:mm:ss",
				L: "DD/MM/YYYY",
				LL: "D [de] MMMM [de] YYYY",
				LLL: "D [de] MMMM [de] YYYY [às] HH:mm",
				LLLL: "dddd, D [de] MMMM [de] YYYY [às] HH:mm"
			},
			relativeTime: {
				future: "em %s",
				past: "há %s",
				s: "poucos segundos",
				m: "um minuto",
				mm: "%d minutos",
				h: "uma hora",
				hh: "%d horas",
				d: "um dia",
				dd: "%d dias",
				M: "um mês",
				MM: "%d meses",
				y: "um ano",
				yy: "%d anos"
			}
		};
		return n.default.locale(r, null, !0), r;
	}));
})), fg = /* @__PURE__ */ c({ default: () => mg }), pg, mg, hg = o((() => {
	Lh(), Ih(), pg = {
		$code: "pt",
		$name: "Português",
		$dir: "ltr",
		carousel: "Carrossel",
		clearEntry: "Limpar entrada",
		close: "Fechar",
		copied: "Copiado",
		copy: "Copiar",
		currentValue: "Valor atual",
		decrement: "Diminuir",
		dropFileHere: "Drop file here or click to browse",
		dropFilesHere: "Drop files here or click to browse",
		error: "Erro",
		goToSlide: (e, t) => `V\xE1 para o slide ${e} de ${t}`,
		hidePassword: "Esconder a senha",
		increment: "Aumentar",
		loading: "Carregando",
		nextSlide: "Próximo slide",
		numOptionsSelected: (e) => e === 0 ? "Nenhuma opção selecionada" : e === 1 ? "1 opção selecionada" : `${e} op\xE7\xF5es selecionadas`,
		pauseAnimation: "Pausar animação",
		playAnimation: "Reproduzir animação",
		previousSlide: "Slide anterior",
		progress: "Progresso",
		remove: "Remover",
		resize: "Mudar o tamanho",
		scrollableRegion: "Região rolável",
		scrollToEnd: "Rolar até o final",
		scrollToStart: "Rolar até o início",
		selectAColorFromTheScreen: "Selecionar uma cor da tela",
		showPassword: "Mostrar senha",
		slideNum: (e) => `Slide ${e}`,
		toggleColorFormat: "Trocar o formato de cor",
		zoomIn: "Aumentar zoom",
		zoomOut: "Diminuir zoom"
	}, Th(pg), mg = pg;
})), gg = Object.freeze([
	{
		value: "en",
		label: "English"
	},
	{
		value: "de",
		label: "Deutsch"
	},
	{
		value: "es",
		label: "Español"
	},
	{
		value: "fr",
		label: "Français"
	},
	{
		value: "pt-BR",
		label: "Português (Brasil)"
	}
]);
function _g(e) {
	return gg.some((t) => t.value === e);
}
var vg = {
	de: async () => {
		let [e] = await Promise.all([
			Promise.resolve().then(() => (Sh(), xh)),
			Promise.resolve().then(() => /* @__PURE__ */ u(wh(), 1)),
			typeof window > "u" ? null : Promise.resolve().then(() => (Hh(), Vh))
		]);
		return e.default;
	},
	es: async () => {
		let [e] = await Promise.all([
			Promise.resolve().then(() => (Kh(), Gh)),
			Promise.resolve().then(() => /* @__PURE__ */ u(qh(), 1)),
			typeof window > "u" ? null : Promise.resolve().then(() => (Zh(), Jh))
		]);
		return e.default;
	},
	fr: async () => {
		let [e] = await Promise.all([
			Promise.resolve().then(() => (tg(), eg)),
			Promise.resolve().then(() => /* @__PURE__ */ u(ng(), 1)),
			typeof window > "u" ? null : Promise.resolve().then(() => (og(), rg))
		]);
		return e.default;
	},
	"pt-BR": async () => {
		let [e] = await Promise.all([
			Promise.resolve().then(() => (ug(), lg)),
			Promise.resolve().then(() => /* @__PURE__ */ u(dg(), 1)),
			typeof window > "u" ? null : Promise.resolve().then(() => (hg(), fg))
		]);
		return e.default;
	}
}, yg = { fr: (e, t) => t === 2 ? Math.abs(e) < 2 ? 0 : 1 : e ? Math.abs(e) === 1 ? 1 : 2 : 0 };
function bg(e = vg) {
	let t = /* @__PURE__ */ new Map([["en", vh]]), n = /* @__PURE__ */ new Map(), r = 0;
	function i(e = "en") {
		return ph({
			legacy: !1,
			globalInjection: !0,
			locale: _g(e) ? e : "en",
			fallbackLocale: "en",
			pluralRules: yg,
			messages: Object.fromEntries([...t].map(([e, t]) => [e, { ...t }]))
		});
	}
	let a = i();
	async function o(r) {
		if (!a.global.availableLocales.includes(r)) {
			if (!_g(r) || !Object.hasOwn(e, r)) throw Error(`No catalog loader for locale: ${r}`);
			if (!n.has(r)) {
				let i = Promise.resolve().then(() => e[r]()).then((e) => {
					t.set(r, e), a.global.setLocaleMessage(r, { ...e });
				}).finally(() => n.delete(r));
				n.set(r, i);
			}
			await n.get(r);
		}
	}
	function s(e, t) {
		a.global.locale.value = e, t && (t.documentElement.lang = e, t.documentElement.style?.setProperty("--twicc-loading-label", JSON.stringify(a.global.t("common.status.loading"))));
	}
	async function c(e, t = globalThis.document) {
		let n = ++r, i = _g(e) ? e : "en";
		if (!a.global.availableLocales.includes(i)) try {
			await o(i);
		} catch (e) {
			if (n !== r) return;
			console.warn(`Could not load locale ${i}; using English.`, e), i = "en";
		}
		if (n === r) return s(i, t), i;
	}
	return {
		createAppI18n: i,
		i18n: a,
		loadLocaleMessages: o,
		setLocale: c
	};
}
var { createAppI18n: xg, i18n: Sg, loadLocaleMessages: Cg, setLocale: wg } = bg(), Tg = {
	format: 1,
	locales: [
		"en",
		"de",
		"es",
		"fr",
		"pt-BR"
	],
	messages: /* @__PURE__ */ JSON.parse("{\"android.instances.subtitle\":{},\"android.instances.saved\":{},\"android.instances.lastUsed\":{},\"android.instances.emptyTitle\":{},\"android.instances.emptyBody\":{},\"android.instances.add\":{},\"android.instances.editTitle\":{},\"android.instances.nameLabel\":{},\"android.instances.addressLabel\":{},\"android.instances.addressHint\":{},\"android.instances.remove\":{},\"android.instances.removeTitle\":{},\"android.instances.removeMessage\":{},\"android.instances.openDescription\":{},\"android.instances.editDescription\":{},\"android.instances.removeDescription\":{},\"android.instances.errors.duplicate\":{},\"android.instances.errors.invalidAddress\":{},\"android.instances.errors.httpsRequired\":{},\"android.instances.errors.originRequired\":{},\"android.instances.errors.rootRequired\":{},\"android.instances.errors.invalidPort\":{},\"android.browser.menuDescription\":{},\"android.browser.version\":{},\"android.browser.errors.engineStart\":{},\"android.browser.errors.rendererStopped\":{},\"android.browser.errors.instanceRemoved\":{},\"android.browser.errors.sessionClosed\":{},\"android.browser.errors.unreachable\":{},\"android.browser.errors.certificate\":{},\"android.browser.errors.httpStatus\":{},\"android.browser.errors.pageBlocked\":{},\"android.browser.errors.webviewProfiles\":{},\"android.browser.errors.linkUnsupported\":{},\"android.browser.errors.browserUnavailable\":{},\"android.browser.errors.applicationUnavailable\":{},\"android.files.errors.pickerUnavailable\":{},\"android.files.errors.folderUnsupported\":{},\"android.files.errors.readFailed\":{},\"android.downloads.browserRequired\":{},\"android.downloads.started\":{},\"android.downloads.saving\":{},\"android.downloads.saved\":{},\"android.downloads.errors.startFailed\":{},\"android.downloads.errors.saveUnavailable\":{},\"android.downloads.errors.failed\":{},\"android.speech.channel\":{},\"android.speech.notificationTitle\":{},\"android.speech.preparing\":{},\"android.speech.reading\":{},\"android.speech.waiting\":{},\"android.speech.reconnecting\":{},\"android.speech.recoveryAnnouncement\":{},\"android.speech.stopping\":{},\"android.speech.skip\":{},\"android.speech.stopAll\":{},\"android.speech.pauseTimeline\":{},\"android.speech.resumeTimeline\":{},\"android.speech.timelinePaused\":{},\"android.speech.notificationsRequired\":{},\"android.speech.batteryTitle\":{},\"android.speech.batteryAdvice\":{},\"android.speech.continueWithoutChanges\":{},\"android.speech.settingsTitle\":{},\"android.speech.openSettings\":{},\"common.actions.ok\":{},\"android.speech.errors.cookieSupport\":{},\"android.instances.title\":{},\"android.speech.errors.updateRequired\":{},\"android.speech.errors.staleDocument\":{},\"android.speech.errors.playbackChanged\":{},\"android.speech.errors.changeFailed\":{},\"android.speech.errors.instanceRequired\":{},\"android.speech.errors.credentialsUnavailable\":{},\"android.speech.errors.stopping\":{},\"android.speech.errors.staleSession\":{},\"android.speech.errors.instanceChanged\":{},\"android.speech.errors.androidStopped\":{},\"android.speech.errors.instanceClosed\":{},\"android.speech.errors.visibleRequired\":{},\"android.speech.errors.batteryChoiceRequired\":{},\"android.speech.errors.generationFailed\":{},\"android.speech.errors.interrupted\":{},\"android.speech.errors.playbackFailed\":{},\"android.speech.errors.retryPlayback\":{},\"android.speech.errors.failed\":{},\"android.speech.errors.audioRequest\":{},\"android.speech.errors.audioEmpty\":{},\"android.speech.errors.audioFormat\":{},\"android.push.soundTitle\":{},\"settings.notifications.sounds.beep\":{},\"settings.notifications.sounds.chime\":{},\"settings.notifications.sounds.ping\":{},\"settings.notifications.sounds.pop\":{},\"settings.notifications.sounds.bloop\":{},\"settings.notifications.sounds.tink\":{},\"settings.notifications.sounds.knock\":{},\"settings.notifications.sounds.sweep\":{},\"settings.notifications.sounds.signal\":{},\"settings.notifications.sounds.ripple\":{},\"android.push.channels.finished\":{},\"android.push.channels.attention\":{},\"android.push.channels.peer\":{},\"android.push.distributorLabel\":{},\"android.push.titleWithInstance\":{},\"android.push.genericTitle\":{},\"android.push.genericBody\":{},\"android.push.errors.updateApp\":{},\"android.push.errors.updateServer\":{},\"android.push.finishedWithShells\":{},\"android.push.extraUsageBody\":{},\"android.push.peerBody\":{},\"android.push.peerBodyWithProject\":{},\"android.push.peerBodyWithSession\":{},\"android.push.peerRequestBody\":{},\"android.language.errors.unsupported\":{},\"android.language.errors.changed\":{},\"android.language.errors.invalidRevision\":{},\"android.language.errors.revisionExhausted\":{},\"android.language.errors.saveFailed\":{},\"android.language.errors.staleDocument\":{},\"android.language.errors.updateApp\":{},\"android.language.errors.updateServer\":{},\"common.actions.save\":{},\"common.actions.cancel\":{},\"common.actions.retry\":{},\"composer.status.startingProvider\":{},\"common.sessionCount\":{\"numberFormats\":{\"count\":\"decimal\"}},\"workspace.projectCount\":{\"numberFormats\":{\"count\":\"decimal\"}},\"common.android.errors.actionFailed\":{},\"common.android.errors.closed\":{},\"common.android.errors.invalidNotification\":{},\"common.android.errors.speechPreparing\":{},\"common.android.errors.tooLarge\":{},\"common.android.errors.unreachable\":{},\"navigation.android.active\":{},\"navigation.android.browser\":{},\"navigation.android.forceRefresh\":{},\"navigation.android.manage\":{},\"navigation.android.menu\":{},\"navigation.android.refresh\":{},\"navigation.android.refreshHint\":{},\"navigation.android.refreshQuestion\":{},\"navigation.android.reload\":{},\"navigation.android.serverHome\":{},\"navigation.android.unsupportedState\":{},\"session.notifications.activityTimeout\":{},\"session.notifications.agentStoppedBeforeAck\":{},\"session.notifications.approval\":{},\"session.notifications.attention\":{},\"session.notifications.body\":{},\"session.notifications.creditsRemaining\":{\"numberFormats\":{\"count\":\"decimal\"}},\"session.notifications.creditsUsed\":{\"numberFormats\":{\"used\":\"decimal\"}},\"session.notifications.creditsUsedOf\":{\"numberFormats\":{\"used\":\"decimal\",\"limit\":\"decimal\"}},\"session.notifications.enabledLabel\":{},\"session.notifications.ephemeral\":{},\"session.notifications.ephemeralSession\":{},\"session.notifications.errors.update\":{},\"session.notifications.extraUsageSentence\":{},\"session.notifications.extraUsageTitle\":{},\"session.notifications.finished\":{},\"session.notifications.instance\":{},\"session.notifications.invalidTitle\":{},\"session.notifications.invalidTitleDetail\":{},\"session.notifications.invalidTitleUnknown\":{},\"session.notifications.moneyUsed\":{\"numberFormats\":{\"used\":\"decimal\"}},\"session.notifications.moneyUsedOf\":{\"numberFormats\":{\"used\":\"decimal\",\"limit\":\"decimal\"}},\"session.notifications.mute\":{},\"session.notifications.mutedLabel\":{},\"session.notifications.muteTooltip\":{},\"session.notifications.notConnected\":{},\"session.notifications.peerAccepted\":{},\"session.notifications.peerDelivered\":{},\"session.notifications.peerDone\":{},\"session.notifications.peerMessage\":{},\"session.notifications.peerRefused\":{},\"session.notifications.peerReply\":{},\"session.notifications.peerRequest\":{},\"session.notifications.peerRequestFrom\":{},\"session.notifications.peerVerification\":{},\"session.notifications.providerDisabled\":{},\"session.notifications.question\":{},\"session.notifications.settingsInvalid\":{},\"session.notifications.shells\":{\"numberFormats\":{\"count\":\"decimal\"}},\"session.notifications.startTimeout\":{},\"session.notifications.terminated\":{},\"session.notifications.turnFinished\":{},\"session.notifications.unknownError\":{},\"session.notifications.unmuteTooltip\":{},\"session.notifications.updateAvailable\":{},\"session.notifications.viewChanges\":{},\"session.speech.errors.cut\":{},\"session.speech.errors.gone\":{},\"session.speech.errors.identityRequired\":{},\"session.speech.errors.initializing\":{},\"session.speech.errors.nativeUnavailable\":{},\"session.speech.errors.noConfig\":{},\"session.speech.errors.refused\":{},\"session.speech.errors.requestFailed\":{},\"session.speech.errors.startFailed\":{},\"session.speech.errors.summaryGone\":{},\"session.speech.errors.unavailable\":{},\"session.speech.errors.unreachable\":{},\"backend.tts.audioGone\":{},\"backend.tts.audioCut\":{},\"backend.tts.textTooLong\":{},\"backend.tts.speedNumber\":{},\"backend.tts.textRequired\":{},\"backend.tts.modelRequired\":{},\"backend.tts.summaryReference\":{},\"backend.tts.summarySavedId\":{},\"backend.tts.summaryFingerprint\":{},\"backend.tts.summaryHashRequiresSaved\":{},\"backend.tts.summaryHandle\":{},\"backend.tts.scriptId\":{},\"backend.tts.configMatches\":{},\"backend.tts.summarySavedGone\":{},\"backend.tts.summaryChanged\":{},\"backend.tts.summaryGone\":{},\"backend.tts.failed\":{},\"backend.tts.nothingToRead\":{},\"backend.tts.configNotSaved\":{},\"backend.tts.configNotValidated\":{},\"backend.tts.scriptMissing\":{},\"backend.tts.pcmRateRequired\":{},\"backend.tts.commandMissing\":{},\"backend.tts.openrouterTimeout\":{},\"backend.tts.openrouterUnreachable\":{},\"backend.tts.summaryStopped\":{},\"backend.tts.stopped\":{},\"backend.tts.partFormatChanged\":{},\"backend.tts.fieldString\":{},\"backend.tts.originChoices\":{},\"backend.tts.formatChoices\":{},\"backend.tts.apiKeyRequired\":{},\"backend.tts.scriptStart\":{},\"backend.tts.scriptStalled\":{},\"backend.tts.scriptAudioLarge\":{},\"backend.tts.scriptOutputPath\":{},\"backend.tts.scriptPathReused\":{},\"backend.tts.scriptUnreadable\":{},\"backend.tts.scriptExit\":{},\"backend.tts.scriptSignal\":{},\"backend.tts.audioExpired\":{},\"backend.tts.authenticationExpired\":{},\"backend.tts.sessionLost\":{},\"backend.tts.invalidCommand\":{},\"backend.tts.scopeUnavailable\":{},\"backend.tts.catchupIncomplete\":{},\"backend.tts.configurationUnavailable\":{},\"backend.tts.notMp3\":{},\"backend.tts.notWav\":{},\"backend.tts.wav16bit\":{},\"backend.tts.wavFormat\":{},\"backend.tts.noAudio\":{},\"backend.tts.chooseMp3\":{},\"backend.tts.chooseWav\":{},\"backend.tts.oggUnsupported\":{},\"backend.tts.flacUnsupported\":{},\"backend.tts.mp4Unsupported\":{},\"backend.tts.openrouterStatus\":{},\"backend.tts.provider400Hint\":{},\"backend.tts.adaptationStopped\":{},\"settings.notifications.targets.extraUsage\":{},\"settings.general.language.label\":{},\"peer.toast.unnamedInstance\":{},\"common.labels.unknown\":{},\"android.push.projectWithParent\":{}}"),
	bootstrapResources: {
		tts_channel: { key: "android.speech.channel" },
		tts_notification_title: {
			key: "android.speech.notificationTitle",
			parameters: { name: "%1$s" }
		},
		tts_preparing: { key: "android.speech.preparing" },
		tts_reading: { key: "android.speech.reading" },
		tts_waiting: { key: "android.speech.waiting" },
		tts_reconnecting: { key: "android.speech.reconnecting" },
		tts_stopping: { key: "android.speech.stopping" },
		tts_skip: { key: "android.speech.skip" },
		tts_stop_all: { key: "android.speech.stopAll" },
		tts_pause_timeline: { key: "android.speech.pauseTimeline" },
		tts_resume_timeline: { key: "android.speech.resumeTimeline" },
		tts_timeline_paused: { key: "android.speech.timelinePaused" },
		tts_notifications_required: { key: "android.speech.notificationsRequired" },
		tts_battery_title: { key: "android.speech.batteryTitle" },
		tts_battery_advice: { key: "android.speech.batteryAdvice" },
		tts_continue_without_changes: { key: "android.speech.continueWithoutChanges" },
		tts_settings_title: { key: "android.speech.settingsTitle" },
		tts_open_settings: { key: "android.speech.openSettings" },
		instances_title: { key: "android.instances.title" },
		instances_subtitle: { key: "android.instances.subtitle" },
		instances_saved: { key: "android.instances.saved" },
		instances_last_used: { key: "android.instances.lastUsed" },
		instances_empty_title: { key: "android.instances.emptyTitle" },
		instances_empty_body: { key: "android.instances.emptyBody" },
		instances_add: { key: "android.instances.add" },
		instances_edit_title: { key: "android.instances.editTitle" },
		instances_name_label: { key: "android.instances.nameLabel" },
		instances_address_label: { key: "android.instances.addressLabel" },
		instances_address_hint: { key: "android.instances.addressHint" },
		instances_save: { key: "common.actions.save" },
		instances_cancel: { key: "common.actions.cancel" },
		instances_remove: { key: "android.instances.remove" },
		instances_remove_title: {
			key: "android.instances.removeTitle",
			parameters: { name: "%1$s" }
		},
		instances_remove_message: { key: "android.instances.removeMessage" },
		instances_open_description: {
			key: "android.instances.openDescription",
			parameters: {
				name: "%1$s",
				address: "%2$s"
			}
		},
		instances_edit_description: {
			key: "android.instances.editDescription",
			parameters: { name: "%1$s" }
		},
		instances_remove_description: {
			key: "android.instances.removeDescription",
			parameters: { name: "%1$s" }
		}
	},
	nativeSources: /* @__PURE__ */ JSON.parse("{\"android/app/src/main/res/values/strings.xml\":{\"Timeline speech\":\"android.speech.channel\",\"%1$s · Speech\":\"android.speech.notificationTitle\",\"Preparing\":\"android.speech.preparing\",\"Reading\":\"android.speech.reading\",\"Waiting\":\"android.speech.waiting\",\"Reconnecting\":\"android.speech.reconnecting\",\"Stopping\":\"android.speech.stopping\",\"Skip\":\"android.speech.skip\",\"Stop all\":\"android.speech.stopAll\",\"Pause timeline\":\"android.speech.pauseTimeline\",\"Resume timeline\":\"android.speech.resumeTimeline\",\"Timeline reading paused\":\"android.speech.timelinePaused\",\"Enable speech notifications to use background Skip and Stop all controls. Then start speech again.\":\"android.speech.notificationsRequired\",\"Timeline listening and battery\":\"android.speech.batteryTitle\",\"You can use timeline listening without changing battery settings. Android may delay or interrupt speech in the background or with the screen off. Allow unrestricted battery use for TwiCC to improve background listening. You can change this later in Android Settings → Apps → TwiCC → Battery.\\\\n\\\\nContinue without changes to start now, or open Settings and then enable listening again.\":\"android.speech.batteryAdvice\",\"Continue without changes\":\"android.speech.continueWithoutChanges\",\"Speech settings required\":\"android.speech.settingsTitle\",\"Open Settings\":\"android.speech.openSettings\",\"TwiCC\":\"android.instances.title\",\"Connect to your existing TwiCC server through an HTTPS tunnel.\":\"android.instances.subtitle\",\"Saved instances\":\"android.instances.saved\",\"Last used\":\"android.instances.lastUsed\",\"No instances yet\":\"android.instances.emptyTitle\",\"Add the address of your TwiCC server to get started.\":\"android.instances.emptyBody\",\"Add instance\":\"android.instances.add\",\"Edit instance\":\"android.instances.editTitle\",\"Name (optional)\":\"android.instances.nameLabel\",\"Server address\":\"android.instances.addressLabel\",\"HTTPS only. A new address starts with a fresh login.\":\"android.instances.addressHint\",\"Save\":\"common.actions.save\",\"Cancel\":\"common.actions.cancel\",\"Remove\":\"android.instances.remove\",\"Remove %1$s?\":\"android.instances.removeTitle\",\"Remove this address and request deletion of its login and Web storage in each engine. The server is unchanged.\":\"android.instances.removeMessage\",\"Open %1$s, %2$s\":\"android.instances.openDescription\",\"Edit %1$s\":\"android.instances.editDescription\",\"Remove %1$s\":\"android.instances.removeDescription\"},\"android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt\":{\"Could not complete this action. Reload this page and try again.\":\"common.android.errors.actionFailed\",\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"Android notifications are unavailable. Update the Android app and try again.\":\"android.push.errors.updateApp\",\"This action contains too much data. Try a smaller selection.\":\"common.android.errors.tooLarge\"},\"android/app/src/main/java/com/twidi/twicc/BrowserActivity.kt\":{\"Android app menu for ${instance.name}\":\"android.browser.menuDescription\",\"${EngineProvider.engine.displayName} could not start. Check the engine installation, then reopen TwiCC.\":\"android.browser.errors.engineStart\",\"This link type is not supported.\":\"android.browser.errors.linkUnsupported\",\"The page stopped unexpectedly. Retry to reopen TwiCC.\":\"android.browser.errors.rendererStopped\",\"Manage instances\":\"navigation.android.manage\",\"TwiCC ${BuildConfig.VERSION_NAME} · ${EngineProvider.engine.displayName}\":\"android.browser.version\",\"Reload page\":\"navigation.android.reload\",\"Reload page (bypass cache)\":\"navigation.android.forceRefresh\",\"Force refresh?\":\"navigation.android.refreshQuestion\",\"Reload this page and bypass its resource cache. Your login and preferences stay saved. Unsaved text may be lost.\":\"navigation.android.refreshHint\",\"Server home\":\"navigation.android.serverHome\",\"Open in browser\":\"navigation.android.browser\",\"This instance is no longer saved.\":\"android.browser.errors.instanceRemoved\",\"The browser session is closed.\":\"android.browser.errors.sessionClosed\",\"Choose how to continue in the battery advice dialog.\":\"android.speech.errors.batteryChoiceRequired\",\"Speech is getting ready. Try again in a moment.\":\"common.android.errors.speechPreparing\",\"Retry\":\"common.actions.retry\",\"Cancel\":\"common.actions.cancel\",\"Refresh\":\"navigation.android.refresh\"},\"android/app/src/main/java/com/twidi/twicc/InstanceStore.kt\":{\"This server address is already saved.\":\"android.instances.errors.duplicate\"},\"android/app/src/main/java/com/twidi/twicc/MainActivity.kt\":{\"Enter a valid HTTPS address.\":\"android.instances.errors.invalidAddress\"},\"android/app/src/main/java/com/twidi/twicc/platform/BrowserDownloads.kt\":{\"This download needs a browser. Open the page in your browser to save blob or data exports.\":\"android.downloads.browserRequired\",\"Download started. Check Android's Downloads notification.\":\"android.downloads.started\",\"The download could not start. Try opening this page in your browser.\":\"android.downloads.errors.startFailed\",\"This download cannot be saved. Try opening the page in your browser.\":\"android.downloads.errors.saveUnavailable\",\"Saving $filename to Downloads…\":\"android.downloads.saving\",\"Saved $filename to Downloads.\":\"android.downloads.saved\",\"Download failed. Check your connection and try again.\":\"android.downloads.errors.failed\"},\"android/app/src/main/java/com/twidi/twicc/platform/ExternalLinks.kt\":{\"This link type is not supported.\":\"android.browser.errors.linkUnsupported\",\"Open in browser\":\"navigation.android.browser\",\"No browser is available to open this link.\":\"android.browser.errors.browserUnavailable\",\"No application can open this link.\":\"android.browser.errors.applicationUnavailable\"},\"android/app/src/main/java/com/twidi/twicc/platform/NativeRequests.kt\":{\"No file picker is available.\":\"android.files.errors.pickerUnavailable\",\"OK\":\"common.actions.ok\",\"Cancel\":\"common.actions.cancel\"},\"android/app/src/main/java/com/twidi/twicc/push/AndroidPush.kt\":{\"Finished work\":\"android.push.channels.finished\",\"Questions and approvals\":\"android.push.channels.attention\",\"Extra usage\":\"settings.notifications.targets.extraUsage\",\"Peer messages and requests\":\"android.push.channels.peer\",\"TwiCC notifications\":\"android.push.distributorLabel\"},\"android/app/src/main/java/com/twidi/twicc/push/AndroidPushService.kt\":{\"${payload.title} · ${saved.name}\":\"android.push.titleWithInstance\"},\"android/app/src/main/java/com/twidi/twicc/tts/NativeTtsBridge.kt\":{\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"Speech is getting ready. Try again in a moment.\":\"common.android.errors.speechPreparing\",\"Reload this page, then try speech again.\":\"android.speech.errors.staleDocument\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\"},\"android/app/src/main/java/com/twidi/twicc/tts/PcmExtractor.kt\":{\"Unsupported speech audio format\":\"android.speech.errors.audioFormat\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsConnection.kt\":{\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"TTS request failed ($status)\":\"android.speech.errors.audioRequest\",\"TTS audio is empty\":\"android.speech.errors.audioEmpty\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsNotification.kt\":{\"Open TwiCC before starting speech.\":\"android.speech.errors.visibleRequired\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsPlayer.kt\":{\"Speech generation failed\":\"android.speech.errors.generationFailed\",\"The audio stopped before the end\":\"session.speech.errors.cut\",\"Audio playback was interrupted.\":\"android.speech.errors.interrupted\",\"Audio playback could not start.\":\"android.speech.errors.playbackFailed\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"The audio is no longer available\":\"session.speech.errors.gone\",\"Could not reach TwiCC. Start playback again.\":\"android.speech.errors.retryPlayback\",\"Unsupported speech audio format\":\"android.speech.errors.audioFormat\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt\":{\"The active instance was closed. Start speech again to continue.\":\"android.speech.errors.instanceClosed\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsServiceOwner.kt\":{\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"The active instance changed.\":\"android.speech.errors.instanceChanged\",\"Open a saved instance before starting speech.\":\"android.speech.errors.instanceRequired\",\"Reload this page, then try speech again.\":\"android.speech.errors.staleDocument\",\"Speech playback changed. Try again.\":\"android.speech.errors.playbackChanged\",\"Could not change speech playback. Reload the page and try again.\":\"android.speech.errors.changeFailed\",\"Reopen this instance, then start speech again.\":\"android.speech.errors.credentialsUnavailable\",\"Speech is stopping. Wait a moment, then try again.\":\"android.speech.errors.stopping\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"This notification belongs to an old speech session.\":\"android.speech.errors.staleSession\",\"Reopen this instance and start speech again.\":\"android.speech.errors.credentialsUnavailable\",\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"Android stopped speech. Start it again.\":\"android.speech.errors.androidStopped\"},\"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoBrowserSession.kt\":{\"The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.\":\"android.browser.errors.certificate\",\"Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.\":\"android.browser.errors.unreachable\",\"Native speech is still initializing. Retry speech.\":\"common.android.errors.speechPreparing\"},\"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoPrompts.kt\":{\"Folder uploads are not available. Select individual files instead.\":\"android.files.errors.folderUnsupported\",\"The selected files could not be read. Try again.\":\"android.files.errors.readFailed\"},\"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewBrowserSession.kt\":{\"This page cannot open inside TwiCC. Retry to return to your server.\":\"android.browser.errors.pageBlocked\",\"Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.\":\"android.browser.errors.unreachable\",\"The server returned HTTP ${response.statusCode}. Check your tunnel or server, then retry.\":\"android.browser.errors.httpStatus\",\"The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.\":\"android.browser.errors.certificate\",\"Folder uploads are not available. Select individual files instead.\":\"android.files.errors.folderUnsupported\",\"Native speech requires a WebView with GET_COOKIE_INFO support. Update Android System WebView.\":\"android.speech.errors.cookieSupport\"},\"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewEngine.kt\":{\"Update Android System WebView or Chrome to use separate instances.\":\"android.browser.errors.webviewProfiles\"},\"android/policy/src/main/kotlin/com/twidi/twicc/InstanceUrl.kt\":{\"Enter a valid HTTPS address.\":\"android.instances.errors.invalidAddress\",\"Use an HTTPS address, such as https://twicc.example.com.\":\"android.instances.errors.httpsRequired\",\"Use the server address without credentials, a query, or a fragment.\":\"android.instances.errors.originRequired\",\"Use the server root address, without a page path.\":\"android.instances.errors.rootRequired\",\"Enter a valid port number.\":\"android.instances.errors.invalidPort\"},\"android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt\":{\"Speech stopped. Start playback or turn on timeline reading again.\":\"backend.tts.sessionLost\",\"The audio stopped before the end\":\"session.speech.errors.cut\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\",\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"The active instance changed\":\"android.speech.errors.instanceChanged\",\"Speech failed\":\"android.speech.errors.failed\"}}"),
	excludedNativeSources: {
		"android/app/src/main/res/values/strings.xml": [{
			text: "TwiCC",
			reason: "app_name is an intentional product name"
		}],
		"android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt": [
			{
				text: "Unsupported Android protocol or request ID",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Missing navigation ID",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Invalid navigation ID",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Unknown Android command",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Missing menu owner",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Missing menu visibility",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Invalid menu owner",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Invalid displayed session",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Invalid displayed session",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/main/java/com/twidi/twicc/BrowserActivity.kt": [{
			text: "Legacy back handling is enabled in the manifest for this Activity.",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}, {
			text: "The platform file picker still uses this Activity result API.",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/main/java/com/twidi/twicc/platform/BrowserDownloads.kt": [{
			text: "Cannot create download",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}, {
			text: "Cannot open download",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/main/java/com/twidi/twicc/push/AndroidPush.kt": [{
			text: "${Build.MODEL} · ${instance.name}",
			reason: "Device model and user instance name; no application-owned prose"
		}],
		"android/app/src/main/java/com/twidi/twicc/tts/NativeTtsBridge.kt": [{
			text: "Unknown speech command",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/main/java/com/twidi/twicc/tts/PcmExtractor.kt": [
			{
				text: "Unsupported PCM parameters",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Duplicate PCM parameter",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Unsupported PCM rate or channels",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech extraction cancelled",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "The PCM stream ends inside an audio frame",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech cannot seek or replay",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/main/java/com/twidi/twicc/tts/TtsConnection.kt": [
			{
				text: "TTS requires the active HTTPS instance.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "TTS requires the active HTTPS instance.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Audio belongs to another instance",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "This audio resource is already open",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech audio opening cancelled",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech audio opening cancelled",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "<!doctype html",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "TTS ending is empty",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "TTS ending is too large",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Invalid TTS ending",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/main/java/com/twidi/twicc/tts/TtsJson.kt": [
			{
				text: "TTS command is too large",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "TTS frame is too large",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Not a JSON value",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/main/java/com/twidi/twicc/tts/TtsPlayer.kt": [
			{
				text: "Speech cannot seek, retry or replay",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech response has no Content-Type",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech response is closed",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech output requires the service main looper",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech extraction requires response headers",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Speech response has no Content-Type",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt": [{
			text: "TTS commands require the service main looper",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoBrowserSession.kt": [
			{
				text: "The browser document is unavailable for cookie export.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "The cookie export bridge disconnected.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "The browser document changed.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Cookie export timed out.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoEngine.kt": [{
			text: "GeckoView 159",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoPrompts.kt": [{
			text: "Cannot read file",
			reason: "Internal file-read exception; GeckoPrompts presents android.files.errors.readFailed instead"
		}],
		"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewBrowserSession.kt": [
			{
				text: "The browser document is unavailable.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Cookie export requires the active HTTPS instance.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "Partitioned authentication cookies are unsupported.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			},
			{
				text: "WebView returned invalid cookie metadata.",
				reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
			}
		],
		"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewEngine.kt": [{
			text: "Android System WebView",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsProtocol.kt": [{
			text: "AudioResource(readingId=$readingId, generation=$generation, audioId=$audioId)",
			reason: "Internal invariant, transport diagnostic, log, or engine product name; keep verbatim"
		}],
		"android/app/src/main/java/com/twidi/twicc/push/PushRegistrationJob.kt": [{
			text: "TwiCC push registration",
			reason: "Worker thread name; never displayed in the interface"
		}]
	}
}, Eg = /* @__PURE__ */ new Map(), Dg = /^-?(?:0|[1-9]\d*)(?:\.(\d+))?$/;
function Og(e, t) {
	if (typeof e == "number") {
		if (!Number.isFinite(e)) return e;
		let t = Object.is(e, -0) ? "-0" : String(e);
		if (!t.includes("e")) e = t;
		else {
			let [n, r] = t.split("e"), i = n.startsWith("-") ? "-" : "", a = n.replace("-", ""), o = (a.indexOf(".") < 0 ? a.length : a.indexOf(".")) + Number(r), s = a.replace(".", "");
			e = i + (o <= 0 ? `0.${"0".repeat(-o)}${s}` : o >= s.length ? s + "0".repeat(o - s.length) : `${s.slice(0, o)}.${s.slice(o)}`);
		}
	}
	if (typeof e != "string" || !Dg.test(e)) return e;
	let [n, r] = e.split("."), i = n === "-0", a = new Intl.NumberFormat(t), o = a.format(i ? -0 : BigInt(n));
	return r === void 0 ? o : `${o}${a.formatToParts(1.1).find((e) => e.type === "decimal").value}${r}`;
}
function kg(e, t, n) {
	let r = Tg.messages[e]?.numberFormats ?? {};
	return Object.fromEntries(Object.entries(t).map(([e, t]) => [e, r[e] === "decimal" ? Og(t, n) : t]));
}
function Ag(e) {
	if (!Eg.has(e)) {
		let { ast: t } = Ue(e, {
			jit: !0,
			optimize: !1,
			onError: (e) => {
				throw e;
			}
		}), n = t.body.type === 1 ? t.body.cases : [t.body];
		Eg.set(e, n.map((e) => e.items.filter((e) => e.type === 4).map((e) => e.key)));
	}
	return Eg.get(e);
}
function jg(e, t = Sg.global) {
	let n = 0;
	function r(e, i) {
		if (i > 4 || ++n > 64 || !e || typeof e != "object" || Array.isArray(e) || typeof e.key != "string" || !t.te(e.key, "en") || e.count !== void 0 && (typeof e.count != "number" || !Number.isFinite(e.count))) throw Error();
		let a = Object.hasOwn(e, "params") ? e.params : {};
		if (!a || typeof a != "object" || Array.isArray(a)) throw Error();
		let o = Object.fromEntries(Object.entries(a).map(([e, t]) => {
			if (typeof t == "object" && t) return [e, r(t, i + 1)];
			if (t !== null && ![
				"string",
				"number",
				"boolean"
			].includes(typeof t) || typeof t == "number" && !Number.isFinite(t)) throw Error();
			return [e, t];
		})), s = t.te(e.key, t.locale.value) ? t.locale.value : "en", c = t.getLocaleMessage(s)[e.key];
		if (typeof c != "string") throw Error();
		let l = Ag(c), u = Math.abs(typeof a.count == "number" ? a.count : typeof a.n == "number" ? a.n : e.count ?? -1), d = l.length === 1 ? 0 : l.length === 2 ? s === "fr" ? u < 2 ? 0 : 1 : u === 1 ? 0 : 1 : s === "fr" ? u === 0 ? 0 : u === 1 ? 1 : 2 : Math.min(u, 2);
		if (!l[d]) throw Error();
		for (let t of l[d]) {
			let n = o[t] ?? (["count", "n"].includes(t) ? e.count : void 0);
			if (n == null || Tg.messages[e.key]?.numberFormats?.[t] === "decimal" && !(typeof n == "number" || typeof n == "string" && Dg.test(n))) throw Error();
		}
		return {
			type: "local-ui-message",
			key: e.key,
			params: o,
			...e.count === void 0 ? {} : { count: e.count }
		};
	}
	try {
		return r(e, 1);
	} catch {
		return null;
	}
}
//#endregion
//#region src/i18n/uiMessageRenderer.js
function Mg(e, t) {
	if (e?.type === "local-ui-message-list") return e.messages.map((e) => Mg(e, t)).join(" — ");
	if (e?.type !== "local-ui-message") return e;
	let n = Object.fromEntries(Object.entries(e.params || {}).map(([e, n]) => [e, n?.type === "local-ui-message" || n?.type === "local-ui-message-list" ? Mg(n, t) : n])), r = typeof n.count == "number" ? n.count : typeof n.n == "number" ? n.n : e.count;
	for (let t of ["count", "n"]) n[t] == null && e.count !== void 0 && Tg.messages[e.key]?.numberFormats?.[t] && (n[t] = e.count);
	let i = kg(e.key, n, t.locale.value);
	for (let e of ["count", "n"]) typeof i[e] == "number" && (i[e] = String(i[e]));
	return t.t(e.key, i, r === void 0 ? {} : r);
}
//#endregion
//#region src/i18n/uiMessages.js
function Ng(e, t = {}, n) {
	return {
		type: "local-ui-message",
		key: e,
		params: t,
		...n === void 0 ? {} : { count: n }
	};
}
function Pg(e) {
	return Mg(e, Sg.global);
}
function Fg(e, t = {}, n) {
	let r = Ng(e, t, n), i = Error(Pg(r));
	return Object.defineProperty(i, "message", {
		configurable: !0,
		get: () => Pg(r)
	}), i.uiMessage = r, i;
}
//#endregion
//#region src/i18n/remoteMessages.js
function Ig(e, t = e?.error ?? e?.message ?? "", n = "ui_message") {
	return jg(e?.[n] ?? (n === "ui_message" ? e?.error_ui_message : void 0)) ?? t;
}
function Lg(e, t = e?.error ?? e?.message ?? "") {
	let n = Ig(e, e?.error ?? e?.message ?? t), r = n?.type === "local-ui-message" ? Fg(n.key, n.params, n.count) : Error(n);
	return e?.code !== void 0 && (r.code = e.code), e?.status !== void 0 && (r.status = e.status), r;
}
//#endregion
//#region src/utils/androidEnvironment.js
var Rg = /* @__PURE__ */ ga(!1);
function zg() {
	Rg.value || (Rg.value = !0, globalThis.window?.dispatchEvent(new Event("twicc:android-ready")));
}
function Bg(e) {
	typeof e?.engine == "string" && (/^Gecko/i.test(e.engine) || /WebView/i.test(e.engine)) && typeof e.version == "string" && e.version;
}
//#endregion
//#region src/utils/androidAppClient.js
var Vg = "twicc.android.response", Hg = "twicc.android.request", Ug = /* @__PURE__ */ new Set([
	"tts.attach",
	"tts.state",
	"tts.acknowledge",
	"tts.manual",
	"tts.listen",
	"tts.set_muted",
	"tts.disable",
	"tts.skip",
	"tts.stop_all"
]), Wg = /* @__PURE__ */ new Set([...Ug, "locale.set"]);
function Gg(e, { timeoutMs: t = 1500 } = {}) {
	let n = /* @__PURE__ */ new Map(), r = /* @__PURE__ */ new Set(), i = Math.random().toString(36).slice(2), a = 0, o = !1, s = null, c = {}, l = typeof e.TwiccAndroid?.postMessage == "function" ? e.TwiccAndroid : null;
	l && zg();
	function u(e) {
		if (o) return;
		if (e?.protocol === 1 && e.event === "stateChanged") {
			for (let e of r) e();
			return;
		}
		if (!e || e.protocol !== 1 || typeof e.id != "string") return;
		let t = n.get(e.id);
		if (t && typeof e.ok == "boolean") {
			if (zg(), n.delete(e.id), clearTimeout(t.timer), e.ok) t.command === "getState" && (c = e.state?.capabilities ?? {}, Bg(e.state)), t.resolve(e.state ?? null);
			else {
				let n = e.ui_message || typeof e.error == "string" ? Lg(e, Fg("common.android.errors.actionFailed").message) : Fg("common.android.errors.actionFailed");
				e.state !== void 0 && (n.state = e.state), t.reject(n);
			}
		}
	}
	function d(t) {
		t.source === e && t.origin === e.location.origin && (t.data?.channel === Vg && u(t.data.response), t.data?.channel === "twicc.android.environment" && t.data.protocol === 1 && typeof t.data.id == "string" && n.get(t.data.id)?.command === "getState" && zg());
	}
	function f(e) {
		try {
			u(JSON.parse(e.data));
		} catch {}
	}
	return e.addEventListener("message", d), l && (l.onmessage = f), {
		supports(e) {
			return c[e] === 1;
		},
		onStateChanged(e) {
			return o || r.add(e), () => r.delete(e);
		},
		async request(r, c = {}) {
			if (o) return Promise.reject(Fg("common.android.errors.closed"));
			if (l && Wg.has(r)) {
				let n = Date.now() + t;
				for (; !s && !o;) {
					let t = e.__twiccNativeTtsDocument;
					if (typeof t == "string" && t) {
						s = t;
						break;
					}
					if (Date.now() >= n) throw Fg(r === "locale.set" ? "android.language.errors.staleDocument" : "common.android.errors.speechPreparing");
					await new Promise((e) => setTimeout(e, 10));
				}
				if (o) throw Fg("common.android.errors.closed");
			}
			let u = `${i}-${++a}`, d = {
				...c,
				...s && Wg.has(r) ? { documentToken: s } : {},
				protocol: 1,
				id: u,
				command: r
			}, f;
			try {
				f = JSON.stringify(d);
				let e = Ug.has(r) ? 12582912 : 16384;
				if (new TextEncoder().encode(f).byteLength > e) return Promise.reject(Fg("common.android.errors.tooLarge"));
			} catch (e) {
				return console.warn("Android request serialization failed", e), Promise.reject(Fg("common.android.errors.actionFailed"));
			}
			return new Promise((i, a) => {
				let o = setTimeout(() => {
					n.delete(u), a(Fg("common.android.errors.unreachable"));
				}, t);
				n.set(u, {
					resolve: i,
					reject: a,
					timer: o,
					command: r
				});
				try {
					l ? l.postMessage(f) : e.postMessage({
						channel: Hg,
						request: d
					}, e.location.origin);
				} catch (e) {
					clearTimeout(o), n.delete(u), console.warn("Android request transport failed", e), a(Fg("common.android.errors.unreachable"));
				}
			});
		},
		close() {
			o = !0, c = {}, r.clear(), e.removeEventListener("message", d), l?.onmessage === f && (l.onmessage = null);
			for (let e of n.values()) clearTimeout(e.timer), e.reject(Fg("common.android.errors.closed"));
			n.clear();
		}
	};
}
var Kg;
function qg() {
	return Kg ?? (Kg = Gg(window));
}
//#endregion
export { Gg as createAndroidAppClient, qg as getAndroidAppClient };
