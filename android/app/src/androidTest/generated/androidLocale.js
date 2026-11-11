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
//#region node_modules/@vue/shared/dist/shared.esm-bundler.js
// @__NO_SIDE_EFFECTS__
function d(e) {
	let t = /* @__PURE__ */ Object.create(null);
	for (let n of e.split(",")) t[n] = 1;
	return (e) => e in t;
}
var f = {}, p = [], m = () => {}, h = () => !1, g = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && (e.charCodeAt(2) > 122 || e.charCodeAt(2) < 97), _ = (e) => e.startsWith("onUpdate:"), v = Object.assign, y = (e, t) => {
	let n = e.indexOf(t);
	n > -1 && e.splice(n, 1);
}, b = Object.prototype.hasOwnProperty, x = (e, t) => b.call(e, t), S = Array.isArray, C = (e) => ne(e) === "[object Map]", w = (e) => ne(e) === "[object Set]", T = (e) => ne(e) === "[object Date]", E = (e) => ne(e) === "[object RegExp]", D = (e) => typeof e == "function", O = (e) => typeof e == "string", k = (e) => typeof e == "symbol", A = (e) => typeof e == "object" && !!e, ee = (e) => (A(e) || D(e)) && D(e.then) && D(e.catch), te = Object.prototype.toString, ne = (e) => te.call(e), re = (e) => ne(e).slice(8, -1), j = (e) => ne(e) === "[object Object]", ie = (e) => O(e) && e !== "NaN" && e[0] !== "-" && "" + parseInt(e, 10) === e, ae = /* @__PURE__ */ d(",key,ref,ref_for,ref_key,onVnodeBeforeMount,onVnodeMounted,onVnodeBeforeUpdate,onVnodeUpdated,onVnodeBeforeUnmount,onVnodeUnmounted"), oe = (e) => {
	let t = /* @__PURE__ */ Object.create(null);
	return ((n) => t[n] || (t[n] = e(n)));
}, se = /-\w/g, M = oe((e) => e.replace(se, (e) => e.slice(1).toUpperCase())), ce = /\B([A-Z])/g, N = oe((e) => e.replace(ce, "-$1").toLowerCase()), le = oe((e) => e.charAt(0).toUpperCase() + e.slice(1)), ue = oe((e) => e ? `on${le(e)}` : ""), P = (e, t) => !Object.is(e, t), de = (e, ...t) => {
	for (let n = 0; n < e.length; n++) e[n](...t);
}, fe = (e, t, n, r = !1) => {
	Object.defineProperty(e, t, {
		configurable: !0,
		enumerable: !1,
		writable: r,
		value: n
	});
}, pe = (e) => {
	let t = parseFloat(e);
	return isNaN(t) ? e : t;
}, me = (e) => {
	let t = O(e) ? Number(e) : NaN;
	return isNaN(t) ? e : t;
}, he, ge = () => he || (he = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : {}), _e = /* @__PURE__ */ d("Infinity,undefined,NaN,isFinite,isNaN,parseFloat,parseInt,decodeURI,decodeURIComponent,encodeURI,encodeURIComponent,Math,Number,Date,Array,Object,Boolean,String,RegExp,Map,Set,JSON,Intl,BigInt,console,Error,Symbol");
function ve(e) {
	if (S(e)) {
		let t = {};
		for (let n = 0; n < e.length; n++) {
			let r = e[n], i = O(r) ? F(r) : ve(r);
			if (i) for (let e in i) t[e] = i[e];
		}
		return t;
	}
	if (O(e) || A(e)) return e;
}
var ye = /;(?![^(]*\))/g, be = /:([^]+)/, xe = /"(?:[^"\\]|\\[^])*"|'(?:[^'\\]|\\[^])*'|\\[^]|\/\*[^]*?\*\//g;
function F(e) {
	let t = {};
	return e.replace(xe, (e) => e.startsWith("/*") ? "" : e).split(ye).forEach((e) => {
		if (e) {
			let n = e.split(be);
			n.length > 1 && (t[n[0].trim()] = n[1].trim());
		}
	}), t;
}
function Se(e) {
	if (!e) return "";
	if (O(e)) return e;
	let t = "";
	for (let n in e) {
		let r = e[n];
		if (O(r) || typeof r == "number") {
			let e = n.startsWith("--") ? n : N(n);
			t += `${e}:${r};`;
		}
	}
	return t;
}
function Ce(e) {
	let t = "";
	if (O(e)) t = e;
	else if (S(e)) for (let n = 0; n < e.length; n++) {
		let r = Ce(e[n]);
		r && (t += r + " ");
	}
	else if (A(e)) for (let n in e) e[n] && (t += n + " ");
	return t.trim();
}
function we(e) {
	if (!e) return null;
	let { class: t, style: n } = e;
	return t && !O(t) && (e.class = Ce(t)), n && (e.style = ve(n)), e;
}
var Te = "itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,readonly", Ee = /* @__PURE__ */ d(Te), De = /* @__PURE__ */ d(Te + ",async,autofocus,autoplay,controls,default,defer,disabled,inert,loop,open,required,reversed,scoped,seamless,checked,muted,multiple,selected");
function Oe(e) {
	return !!e || e === "";
}
var ke = /* @__PURE__ */ d("accept,accept-charset,accesskey,action,align,allow,alt,async,autocapitalize,autocomplete,autofocus,autoplay,background,bgcolor,border,buffered,capture,challenge,charset,checked,cite,class,code,codebase,color,cols,colspan,content,contenteditable,contextmenu,controls,coords,crossorigin,csp,data,datetime,decoding,default,defer,dir,dirname,disabled,download,draggable,dropzone,enctype,enterkeyhint,for,form,formaction,formenctype,formmethod,formnovalidate,formtarget,headers,height,hidden,high,href,hreflang,http-equiv,icon,id,importance,inert,integrity,ismap,itemprop,keytype,kind,label,lang,language,loading,list,loop,low,manifest,max,maxlength,minlength,media,min,multiple,muted,name,novalidate,open,optimum,pattern,ping,placeholder,poster,preload,radiogroup,readonly,referrerpolicy,rel,required,reversed,rows,rowspan,sandbox,scope,scoped,selected,shape,size,sizes,slot,span,spellcheck,src,srcdoc,srclang,srcset,start,step,style,summary,tabindex,target,title,translate,type,usemap,value,width,wrap"), Ae = /* @__PURE__ */ d("xmlns,accent-height,accumulate,additive,alignment-baseline,alphabetic,amplitude,arabic-form,ascent,attributeName,attributeType,azimuth,baseFrequency,baseline-shift,baseProfile,bbox,begin,bias,by,calcMode,cap-height,class,clip,clipPathUnits,clip-path,clip-rule,color,color-interpolation,color-interpolation-filters,color-profile,color-rendering,contentScriptType,contentStyleType,crossorigin,cursor,cx,cy,d,decelerate,descent,diffuseConstant,direction,display,divisor,dominant-baseline,dur,dx,dy,edgeMode,elevation,enable-background,end,exponent,fill,fill-opacity,fill-rule,filter,filterRes,filterUnits,flood-color,flood-opacity,font-family,font-size,font-size-adjust,font-stretch,font-style,font-variant,font-weight,format,from,fr,fx,fy,g1,g2,glyph-name,glyph-orientation-horizontal,glyph-orientation-vertical,glyphRef,gradientTransform,gradientUnits,hanging,height,href,hreflang,horiz-adv-x,horiz-origin-x,id,ideographic,image-rendering,in,in2,intercept,k,k1,k2,k3,k4,kernelMatrix,kernelUnitLength,kerning,keyPoints,keySplines,keyTimes,lang,lengthAdjust,letter-spacing,lighting-color,limitingConeAngle,local,marker-end,marker-mid,marker-start,markerHeight,markerUnits,markerWidth,mask,maskContentUnits,maskUnits,mathematical,max,media,method,min,mode,name,numOctaves,offset,opacity,operator,order,orient,orientation,origin,overflow,overline-position,overline-thickness,panose-1,paint-order,path,pathLength,patternContentUnits,patternTransform,patternUnits,ping,pointer-events,points,pointsAtX,pointsAtY,pointsAtZ,preserveAlpha,preserveAspectRatio,primitiveUnits,r,radius,referrerPolicy,refX,refY,rel,rendering-intent,repeatCount,repeatDur,requiredExtensions,requiredFeatures,restart,result,rotate,rx,ry,scale,seed,shape-rendering,slope,spacing,specularConstant,specularExponent,speed,spreadMethod,startOffset,stdDeviation,stemh,stemv,stitchTiles,stop-color,stop-opacity,strikethrough-position,strikethrough-thickness,string,stroke,stroke-dasharray,stroke-dashoffset,stroke-linecap,stroke-linejoin,stroke-miterlimit,stroke-opacity,stroke-width,style,surfaceScale,systemLanguage,tabindex,tableValues,target,targetX,targetY,text-anchor,text-decoration,text-rendering,textLength,to,transform,transform-origin,type,u1,u2,underline-position,underline-thickness,unicode,unicode-bidi,unicode-range,units-per-em,v-alphabetic,v-hanging,v-ideographic,v-mathematical,values,vector-effect,version,vert-adv-y,vert-origin-x,vert-origin-y,viewBox,viewTarget,visibility,width,widths,word-spacing,writing-mode,x,x-height,x1,x2,xChannelSelector,xlink:actuate,xlink:arcrole,xlink:href,xlink:role,xlink:show,xlink:title,xlink:type,xmlns:xlink,xml:base,xml:lang,xml:space,y,y1,y2,yChannelSelector,z,zoomAndPan");
function je(e) {
	if (e == null) return !1;
	let t = typeof e;
	return t === "string" || t === "number" || t === "boolean";
}
var Me = /[ !"#$%&'()*+,./:;<=>?@[\\\]^`{|}~]/g;
function Ne(e, t) {
	return e.replace(Me, (e) => t ? e === "\"" ? "\\\\\\\"" : `\\\\${e}` : `\\${e}`);
}
function Pe(e, t, n) {
	if (e.length !== t.length) return !1;
	let r = !0;
	for (let i = 0; r && i < e.length; i++) r = Re(e[i], t[i], n);
	return r;
}
function Fe(e, t, n) {
	if (e.size !== t.size) return !1;
	let r = Array.from(t), i = new Uint8Array(r.length);
	for (let t of e) {
		let e = -1;
		for (let a = 0; a < r.length; a++) if (!i[a] && Re(t, r[a], n)) {
			e = a;
			break;
		}
		if (e < 0) return !1;
		i[e] = 1;
	}
	return !0;
}
function Ie(e, t, n) {
	let r = C(e), i = C(t);
	if (r || i || (r = w(e), i = w(t), r || i)) return r && i ? Fe(e, t, n) : !1;
	if (Object.keys(e).length !== Object.keys(t).length) return !1;
	for (let r in e) {
		let i = e.hasOwnProperty(r), a = t.hasOwnProperty(r);
		if (i && !a || !i && a || !Re(e[r], t[r], n)) return !1;
	}
	return String(e) === String(t);
}
function Le(e, t, n, r) {
	n || (n = [/* @__PURE__ */ new Map(), /* @__PURE__ */ new Map()]);
	let [i, a] = n;
	if (i.has(e) || a.has(t)) return i.get(e) === t && a.get(t) === e;
	i.set(e, t), a.set(t, e);
	let o = r(e, t, n);
	return i.delete(e), a.delete(t), o;
}
function Re(e, t, n) {
	if (e === t) return !0;
	let r = T(e), i = T(t);
	return r || i ? r && i ? e.getTime() === t.getTime() : !1 : (r = k(e), i = k(t), r || i ? e === t : (r = S(e), i = S(t), r || i ? r && i ? Le(e, t, n, Pe) : !1 : (r = A(e), i = A(t), r || i ? !r || !i ? !1 : Le(e, t, n, Ie) : String(e) === String(t))));
}
function ze(e, t) {
	return e.findIndex((e) => Re(e, t));
}
var Be = (e) => !!(e && e.__v_isRef === !0), Ve = (e) => O(e) ? e : e == null ? "" : S(e) || A(e) && (e.toString === te || !D(e.toString)) ? Be(e) ? Ve(e.value) : JSON.stringify(e, He, 2) : String(e), He = (e, t) => Be(t) ? He(e, t.value) : C(t) ? { [`Map(${t.size})`]: [...t.entries()].reduce((e, [t, n], r) => (e[Ue(t, r) + " =>"] = n, e), {}) } : w(t) ? { [`Set(${t.size})`]: [...t.values()].map((e) => Ue(e)) } : k(t) ? Ue(t) : A(t) && !S(t) && !j(t) ? String(t) : t, Ue = (e, t = "") => k(e) ? `Symbol(${e.description ?? t})` : e;
function We(e) {
	return e == null ? "initial" : typeof e == "string" ? e === "" ? " " : e : String(e);
}
//#endregion
//#region node_modules/@vue/reactivity/dist/reactivity.esm-bundler.js
var I, Ge = class {
	constructor(e = !1) {
		this.detached = e, this._active = !0, this._on = 0, this.effects = [], this.cleanups = [], this._isPaused = !1, this._warnOnRun = !0, this.__v_skip = !0, !e && I && (I.active ? (this.parent = I, this.index = (I.scopes || (I.scopes = [])).push(this) - 1) : (this._active = !1, this._warnOnRun = !1));
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
			let t = I;
			try {
				return I = this, e();
			} finally {
				I = t;
			}
		}
	}
	on() {
		++this._on === 1 && (this.prevScope = I, I = this);
	}
	off() {
		if (this._on > 0 && --this._on === 0) {
			if (I === this) I = this.prevScope;
			else {
				let e = I;
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
function Ke(e) {
	return new Ge(e);
}
function qe() {
	return I;
}
function Je(e, t = !1) {
	I && I.cleanups.push(e);
}
var L, Ye = /* @__PURE__ */ new WeakSet(), Xe = class {
	constructor(e) {
		this.fn = e, this.deps = void 0, this.depsTail = void 0, this.flags = 5, this.next = void 0, this.cleanup = void 0, this.scheduler = void 0, I && (I.active ? I.effects.push(this) : this.flags &= -2);
	}
	pause() {
		this.flags |= 64;
	}
	resume() {
		this.flags & 64 && (this.flags &= -65, Ye.has(this) && (Ye.delete(this), this.trigger()));
	}
	notify() {
		this.flags & 2 && !(this.flags & 32) || this.flags & 8 || et(this);
	}
	run() {
		if (!(this.flags & 1)) return this.fn();
		this.flags |= 2, ht(this), rt(this);
		let e = L, t = dt;
		L = this, dt = !0;
		try {
			return this.fn();
		} finally {
			it(this), L = e, dt = t, this.flags &= -3;
		}
	}
	stop() {
		if (this.flags & 1) {
			for (let e = this.deps; e; e = e.nextDep) st(e);
			this.deps = this.depsTail = void 0, ht(this), this.onStop && this.onStop(), this.flags &= -2;
		}
	}
	trigger() {
		this.flags & 64 ? Ye.add(this) : this.scheduler ? this.scheduler() : this.runIfDirty();
	}
	runIfDirty() {
		at(this) && this.run();
	}
	get dirty() {
		return at(this);
	}
}, Ze = 0, Qe, $e;
function et(e, t = !1) {
	e.flags |= 8, t ? (e.next = $e, $e = e) : (e.next = Qe, Qe = e);
}
function tt() {
	Ze++;
}
function nt() {
	if (--Ze > 0) return;
	if ($e) {
		let e = $e;
		for ($e = void 0; e;) {
			let t = e.next;
			e.next = void 0, e.flags &= -9, e = t;
		}
	}
	let e;
	for (; Qe;) {
		let t = Qe;
		for (Qe = void 0; t;) {
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
function rt(e) {
	for (let t = e.deps; t; t = t.nextDep) t.version = -1, t.prevActiveLink = t.dep.activeLink, t.dep.activeLink = t;
}
function it(e) {
	let t, n = e.depsTail, r = n;
	for (; r;) {
		let e = r.prevDep;
		r.version === -1 ? (r === n && (n = e), st(r), ct(r)) : t = r, r.dep.activeLink = r.prevActiveLink, r.prevActiveLink = void 0, r = e;
	}
	e.deps = t, e.depsTail = n;
}
function at(e) {
	for (let t = e.deps; t; t = t.nextDep) if (t.dep.version !== t.version || t.dep.computed && (ot(t.dep.computed) || t.dep.version !== t.version)) return !0;
	return !!e._dirty;
}
function ot(e) {
	if (e.flags & 4 && !(e.flags & 16) || (e.flags &= -17, e.globalVersion === gt) || (e.globalVersion = gt, !e.isSSR && e.flags & 128 && (!e.deps && !e._dirty || !at(e)))) return;
	e.flags |= 2;
	let t = e.dep, n = L, r = dt;
	L = e, dt = !0;
	try {
		rt(e);
		let n = e.fn(e._value);
		(t.version === 0 || P(n, e._value)) && (e.flags |= 128, e._value = n, t.version++);
	} catch (e) {
		throw t.version++, e;
	} finally {
		L = n, dt = r, it(e), e.flags &= -3;
	}
}
function st(e, t = !1) {
	let { dep: n, prevSub: r, nextSub: i } = e;
	if (r && (r.nextSub = i, e.prevSub = void 0), i && (i.prevSub = r, e.nextSub = void 0), n.subs === e && (n.subs = r, !r && n.computed)) {
		n.computed.flags &= -5;
		for (let e = n.computed.deps; e; e = e.nextDep) st(e, !0);
	}
	!t && !--n.sc && n.map && n.map.delete(n.key);
}
function ct(e) {
	let { prevDep: t, nextDep: n } = e;
	t && (t.nextDep = n, e.prevDep = void 0), n && (n.prevDep = t, e.nextDep = void 0);
}
function lt(e, t) {
	e.effect instanceof Xe && (e = e.effect.fn);
	let n = new Xe(e);
	t && v(n, t);
	try {
		n.run();
	} catch (e) {
		throw n.stop(), e;
	}
	let r = n.run.bind(n);
	return r.effect = n, r;
}
function ut(e) {
	e.effect.stop();
}
var dt = !0, ft = [];
function pt() {
	ft.push(dt), dt = !1;
}
function mt() {
	let e = ft.pop();
	dt = e === void 0 || e;
}
function ht(e) {
	let { cleanup: t } = e;
	if (e.cleanup = void 0, t) {
		let e = L;
		L = void 0;
		try {
			t();
		} finally {
			L = e;
		}
	}
}
var gt = 0, _t = class {
	constructor(e, t) {
		this.sub = e, this.dep = t, this.version = t.version, this.nextDep = this.prevDep = this.nextSub = this.prevSub = this.prevActiveLink = void 0;
	}
}, vt = class {
	constructor(e) {
		this.computed = e, this.version = 0, this.activeLink = void 0, this.subs = void 0, this.map = void 0, this.key = void 0, this.sc = 0, this.__v_skip = !0;
	}
	track(e) {
		if (!L || !dt || L === this.computed) return;
		let t = this.activeLink;
		if (t === void 0 || t.sub !== L) t = this.activeLink = new _t(L, this), L.deps ? (t.prevDep = L.depsTail, L.depsTail.nextDep = t, L.depsTail = t) : L.deps = L.depsTail = t, yt(t);
		else if (t.version === -1 && (t.version = this.version, t.nextDep)) {
			let e = t.nextDep;
			e.prevDep = t.prevDep, t.prevDep && (t.prevDep.nextDep = e), t.prevDep = L.depsTail, t.nextDep = void 0, L.depsTail.nextDep = t, L.depsTail = t, L.deps === t && (L.deps = e);
		}
		return t;
	}
	trigger(e) {
		this.version++, gt++, this.notify(e);
	}
	notify(e) {
		tt();
		try {
			for (let e = this.subs; e; e = e.prevSub) e.sub.notify() && e.sub.dep.notify();
		} finally {
			nt();
		}
	}
};
function yt(e) {
	if (e.dep.sc++, e.sub.flags & 4) {
		let t = e.dep.computed;
		if (t && !e.dep.subs) {
			t.flags |= 20;
			for (let e = t.deps; e; e = e.nextDep) yt(e);
		}
		let n = e.dep.subs;
		n !== e && (e.prevSub = n, n && (n.nextSub = e)), e.dep.subs = e;
	}
}
var bt = /* @__PURE__ */ new WeakMap(), xt = /* @__PURE__ */ Symbol(""), St = /* @__PURE__ */ Symbol(""), Ct = /* @__PURE__ */ Symbol("");
function wt(e, t, n) {
	if (dt && L) {
		let t = bt.get(e);
		t || bt.set(e, t = /* @__PURE__ */ new Map());
		let r = t.get(n);
		r || (t.set(n, r = new vt()), r.map = t, r.key = n), r.track();
	}
}
function Tt(e, t, n, r, i, a) {
	let o = bt.get(e);
	if (!o) {
		gt++;
		return;
	}
	let s = (e) => {
		e && e.trigger();
	};
	if (tt(), t === "clear") o.forEach(s);
	else {
		let i = S(e), a = i && ie(n);
		if (i && n === "length") {
			let e = Number(r);
			o.forEach((t, n) => {
				(n === "length" || n === Ct || !k(n) && n >= e) && s(t);
			});
		} else switch ((n !== void 0 || o.has(void 0)) && s(o.get(n)), a && s(o.get(Ct)), t) {
			case "add":
				i ? a && s(o.get("length")) : (s(o.get(xt)), C(e) && s(o.get(St)));
				break;
			case "delete":
				i || (s(o.get(xt)), C(e) && s(o.get(St)));
				break;
			case "set": C(e) && s(o.get(xt));
		}
	}
	nt();
}
function Et(e, t) {
	let n = bt.get(e);
	return n && n.get(t);
}
function Dt(e) {
	let t = /* @__PURE__ */ R(e);
	return t === e || (wt(t, "iterate", Ct), /* @__PURE__ */ gn(e)) ? t : /* @__PURE__ */ hn(e) ? /* @__PURE__ */ mn(e) ? t.map((e) => bn(yn(e))) : t.map(bn) : t.map(yn);
}
function Ot(e) {
	return wt(e = /* @__PURE__ */ R(e), "iterate", Ct), e;
}
function kt(e, t) {
	return /* @__PURE__ */ hn(e) ? bn(/* @__PURE__ */ mn(e) ? yn(t) : t) : yn(t);
}
var At = {
	__proto__: null,
	[Symbol.iterator]() {
		return jt(this, Symbol.iterator, (e) => kt(this, e));
	},
	concat(...e) {
		return Dt(this).concat(...e.map((e) => S(e) ? Dt(e) : e));
	},
	entries() {
		return jt(this, "entries", (e) => (e[1] = kt(this, e[1]), e));
	},
	every(e, t) {
		return Nt(this, "every", e, t, void 0, arguments);
	},
	filter(e, t) {
		return Nt(this, "filter", e, t, (e) => e.map((e) => kt(this, e)), arguments);
	},
	find(e, t) {
		return Nt(this, "find", e, t, (e) => kt(this, e), arguments);
	},
	findIndex(e, t) {
		return Nt(this, "findIndex", e, t, void 0, arguments);
	},
	findLast(e, t) {
		return Nt(this, "findLast", e, t, (e) => kt(this, e), arguments);
	},
	findLastIndex(e, t) {
		return Nt(this, "findLastIndex", e, t, void 0, arguments);
	},
	forEach(e, t) {
		return Nt(this, "forEach", e, t, void 0, arguments);
	},
	includes(...e) {
		return Ft(this, "includes", e);
	},
	indexOf(...e) {
		return Ft(this, "indexOf", e);
	},
	join(e) {
		return Dt(this).join(e);
	},
	lastIndexOf(...e) {
		return Ft(this, "lastIndexOf", e);
	},
	map(e, t) {
		return Nt(this, "map", e, t, void 0, arguments);
	},
	pop() {
		return It(this, "pop");
	},
	push(...e) {
		return It(this, "push", e);
	},
	reduce(e, ...t) {
		return Pt(this, "reduce", e, t);
	},
	reduceRight(e, ...t) {
		return Pt(this, "reduceRight", e, t);
	},
	shift() {
		return It(this, "shift");
	},
	some(e, t) {
		return Nt(this, "some", e, t, void 0, arguments);
	},
	splice(...e) {
		return It(this, "splice", e);
	},
	toReversed() {
		return Dt(this).toReversed();
	},
	toSorted(e) {
		return Dt(this).toSorted(e);
	},
	toSpliced(...e) {
		return Dt(this).toSpliced(...e);
	},
	unshift(...e) {
		return It(this, "unshift", e);
	},
	values() {
		return jt(this, "values", (e) => kt(this, e));
	}
};
function jt(e, t, n) {
	let r = Ot(e), i = r[t]();
	return r !== e && !/* @__PURE__ */ gn(e) && (i._next = i.next, i.next = () => {
		let e = i._next();
		return e.done || (e.value = n(e.value)), e;
	}), i;
}
var Mt = Array.prototype;
function Nt(e, t, n, r, i, a) {
	let o = Ot(e), s = o !== e && !/* @__PURE__ */ gn(e), c = o[t];
	if (c !== Mt[t]) {
		let t = c.apply(e, a);
		return s ? yn(t) : t;
	}
	let l = n;
	o !== e && (s ? l = function(t, r) {
		return n.call(this, kt(e, t), r, e);
	} : n.length > 2 && (l = function(t, r) {
		return n.call(this, t, r, e);
	}));
	let u = c.call(o, l, r);
	return s && i ? i(u) : u;
}
function Pt(e, t, n, r) {
	let i = Ot(e), a = i !== e && !/* @__PURE__ */ gn(e), o = n, s = !1;
	i !== e && (a ? (s = r.length === 0, o = function(t, r, i) {
		return s && (s = !1, t = kt(e, t)), n.call(this, t, kt(e, r), i, e);
	}) : n.length > 3 && (o = function(t, r, i) {
		return n.call(this, t, r, i, e);
	}));
	let c = i[t](o, ...r);
	return s ? kt(e, c) : c;
}
function Ft(e, t, n) {
	let r = /* @__PURE__ */ R(e);
	wt(r, "iterate", Ct);
	let i = r[t](...n);
	return (i === -1 || i === !1) && /* @__PURE__ */ _n(n[0]) ? (n[0] = /* @__PURE__ */ R(n[0]), r[t](...n)) : i;
}
function It(e, t, n = []) {
	pt(), tt();
	let r = (/* @__PURE__ */ R(e))[t].apply(e, n);
	return nt(), mt(), r;
}
var Lt = /* @__PURE__ */ d("__proto__,__v_isRef,__isVue"), Rt = new Set(/* @__PURE__ */ Object.getOwnPropertyNames(Symbol).filter((e) => e !== "arguments" && e !== "caller").map((e) => Symbol[e]).filter(k));
function zt(e) {
	k(e) || (e = String(e));
	let t = /* @__PURE__ */ R(this);
	return wt(t, "has", e), t.hasOwnProperty(e);
}
var Bt = class {
	constructor(e = !1, t = !1) {
		this._isReadonly = e, this._isShallow = t;
	}
	get(e, t, n) {
		if (t === "__v_skip") return e.__v_skip;
		let r = this._isReadonly, i = this._isShallow;
		if (t === "__v_isReactive") return !r;
		if (t === "__v_isReadonly") return r;
		if (t === "__v_isShallow") return i;
		if (t === "__v_raw") return n === (r ? i ? sn : on : i ? an : rn).get(e) || Object.getPrototypeOf(e) === Object.getPrototypeOf(n) ? e : void 0;
		let a = S(e);
		if (!r) {
			let e;
			if (a && (e = At[t])) return e;
			if (t === "hasOwnProperty") return zt;
		}
		let o = Reflect.get(e, t, /* @__PURE__ */ z(e) ? e : n);
		if ((k(t) ? Rt.has(t) : Lt(t)) || (r || wt(e, "get", t), i)) return o;
		if (/* @__PURE__ */ z(o)) {
			let e = a && ie(t) ? o : o.value;
			return r && A(e) ? /* @__PURE__ */ dn(e) : e;
		}
		return A(o) ? r ? /* @__PURE__ */ dn(o) : /* @__PURE__ */ ln(o) : o;
	}
}, Vt = class extends Bt {
	constructor(e = !1) {
		super(!1, e);
	}
	set(e, t, n, r) {
		let i = e[t], a = S(e) && ie(t);
		if (!this._isShallow) {
			let e = /* @__PURE__ */ hn(i);
			if (!/* @__PURE__ */ gn(n) && !/* @__PURE__ */ hn(n) && (i = /* @__PURE__ */ R(i), n = /* @__PURE__ */ R(n)), !a && /* @__PURE__ */ z(i) && !/* @__PURE__ */ z(n)) return e || (i.value = n), !0;
		}
		let o = a ? Number(t) < e.length : x(e, t), s = Reflect.set(e, t, n, /* @__PURE__ */ z(e) ? e : r);
		return e === /* @__PURE__ */ R(r) && s && (o ? P(n, i) && Tt(e, "set", t, n, i) : Tt(e, "add", t, n)), s;
	}
	deleteProperty(e, t) {
		let n = x(e, t), r = e[t], i = Reflect.deleteProperty(e, t);
		return i && n && Tt(e, "delete", t, void 0, r), i;
	}
	has(e, t) {
		let n = Reflect.has(e, t);
		return (!k(t) || !Rt.has(t)) && wt(e, "has", t), n;
	}
	ownKeys(e) {
		return wt(e, "iterate", S(e) ? "length" : xt), Reflect.ownKeys(e);
	}
}, Ht = class extends Bt {
	constructor(e = !1) {
		super(!0, e);
	}
	set(e, t) {
		return !0;
	}
	deleteProperty(e, t) {
		return !0;
	}
}, Ut = /* @__PURE__ */ new Vt(), Wt = /* @__PURE__ */ new Ht(), Gt = /* @__PURE__ */ new Vt(!0), Kt = /* @__PURE__ */ new Ht(!0), qt = (e) => e, Jt = (e) => Reflect.getPrototypeOf(e);
function Yt(e, t, n) {
	return function(...r) {
		let i = this.__v_raw, a = /* @__PURE__ */ R(i), o = C(a), s = e === "entries" || e === Symbol.iterator && o, c = e === "keys" && o, l = i[e](...r), u = n ? qt : t ? bn : yn;
		return !t && wt(a, "iterate", c ? St : xt), v(Object.create(l), { next() {
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
function Xt(e) {
	return function(...t) {
		return e === "delete" ? !1 : e === "clear" ? void 0 : this;
	};
}
function Zt(e, t) {
	let n = {
		get(n) {
			let r = this.__v_raw, i = /* @__PURE__ */ R(r), a = /* @__PURE__ */ R(n);
			e || (P(n, a) && wt(i, "get", n), wt(i, "get", a));
			let { has: o } = Jt(i), s = t ? qt : e ? bn : yn;
			if (o.call(i, n)) return s(r.get(n));
			if (o.call(i, a)) return s(r.get(a));
			r !== i && r.get(n);
		},
		get size() {
			let t = this.__v_raw;
			return !e && wt(/* @__PURE__ */ R(t), "iterate", xt), t.size;
		},
		has(t) {
			let n = this.__v_raw, r = /* @__PURE__ */ R(n), i = /* @__PURE__ */ R(t);
			return e || (P(t, i) && wt(r, "has", t), wt(r, "has", i)), t === i ? n.has(t) : n.has(t) || n.has(i);
		},
		forEach(n, r) {
			let i = this, a = i.__v_raw, o = /* @__PURE__ */ R(a), s = t ? qt : e ? bn : yn;
			return !e && wt(o, "iterate", xt), a.forEach((e, t) => n.call(r, s(e), s(t), i));
		}
	};
	return v(n, e ? {
		add: Xt("add"),
		set: Xt("set"),
		delete: Xt("delete"),
		clear: Xt("clear")
	} : {
		add(e) {
			let n = /* @__PURE__ */ R(this), r = Jt(n), i = /* @__PURE__ */ R(e), a = !t && !/* @__PURE__ */ gn(e) && !/* @__PURE__ */ hn(e) ? i : e;
			return r.has.call(n, a) || P(e, a) && r.has.call(n, e) || P(i, a) && r.has.call(n, i) || (n.add(a), Tt(n, "add", a, a)), this;
		},
		set(e, n) {
			!t && !/* @__PURE__ */ gn(n) && !/* @__PURE__ */ hn(n) && (n = /* @__PURE__ */ R(n));
			let r = /* @__PURE__ */ R(this), { has: i, get: a } = Jt(r), o = i.call(r, e);
			o || (e = /* @__PURE__ */ R(e), o = i.call(r, e));
			let s = a.call(r, e);
			return r.set(e, n), o ? P(n, s) && Tt(r, "set", e, n, s) : Tt(r, "add", e, n), this;
		},
		delete(e) {
			let t = /* @__PURE__ */ R(this), { has: n, get: r } = Jt(t), i = n.call(t, e);
			i || (e = /* @__PURE__ */ R(e), i = n.call(t, e));
			let a = r ? r.call(t, e) : void 0, o = t.delete(e);
			return i && Tt(t, "delete", e, void 0, a), o;
		},
		clear() {
			let e = /* @__PURE__ */ R(this), t = e.size !== 0, n = e.clear();
			return t && Tt(e, "clear", void 0, void 0, void 0), n;
		}
	}), [
		"keys",
		"values",
		"entries",
		Symbol.iterator
	].forEach((r) => {
		n[r] = Yt(r, e, t);
	}), n;
}
function Qt(e, t) {
	let n = Zt(e, t);
	return (t, r, i) => r === "__v_isReactive" ? !e : r === "__v_isReadonly" ? e : r === "__v_raw" ? t : Reflect.get(x(n, r) && r in t ? n : t, r, i);
}
var $t = { get: /* @__PURE__ */ Qt(!1, !1) }, en = { get: /* @__PURE__ */ Qt(!1, !0) }, tn = { get: /* @__PURE__ */ Qt(!0, !1) }, nn = { get: /* @__PURE__ */ Qt(!0, !0) }, rn = /* @__PURE__ */ new WeakMap(), an = /* @__PURE__ */ new WeakMap(), on = /* @__PURE__ */ new WeakMap(), sn = /* @__PURE__ */ new WeakMap();
function cn(e) {
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
function ln(e) {
	return /* @__PURE__ */ hn(e) ? e : pn(e, !1, Ut, $t, rn);
}
// @__NO_SIDE_EFFECTS__
function un(e) {
	return pn(e, !1, Gt, en, an);
}
// @__NO_SIDE_EFFECTS__
function dn(e) {
	return pn(e, !0, Wt, tn, on);
}
// @__NO_SIDE_EFFECTS__
function fn(e) {
	return pn(e, !0, Kt, nn, sn);
}
function pn(e, t, n, r, i) {
	if (!A(e) || e.__v_raw && !(t && e.__v_isReactive) || e.__v_skip || !Object.isExtensible(e)) return e;
	let a = i.get(e);
	if (a) return a;
	let o = cn(re(e));
	if (o === 0) return e;
	let s = new Proxy(e, o === 2 ? r : n);
	return i.set(e, s), s;
}
// @__NO_SIDE_EFFECTS__
function mn(e) {
	return /* @__PURE__ */ hn(e) ? /* @__PURE__ */ mn(e.__v_raw) : !!(e && e.__v_isReactive);
}
// @__NO_SIDE_EFFECTS__
function hn(e) {
	return !!(e && e.__v_isReadonly);
}
// @__NO_SIDE_EFFECTS__
function gn(e) {
	return !!(e && e.__v_isShallow);
}
// @__NO_SIDE_EFFECTS__
function _n(e) {
	return e ? !!e.__v_raw : !1;
}
// @__NO_SIDE_EFFECTS__
function R(e) {
	let t = e && e.__v_raw;
	return t ? /* @__PURE__ */ R(t) : e;
}
function vn(e) {
	return !x(e, "__v_skip") && Object.isExtensible(e) && fe(e, "__v_skip", !0), e;
}
var yn = (e) => A(e) ? /* @__PURE__ */ ln(e) : e, bn = (e) => A(e) ? /* @__PURE__ */ dn(e) : e;
// @__NO_SIDE_EFFECTS__
function z(e) {
	return e ? e.__v_isRef === !0 : !1;
}
// @__NO_SIDE_EFFECTS__
function xn(e) {
	return Cn(e, !1);
}
// @__NO_SIDE_EFFECTS__
function Sn(e) {
	return Cn(e, !0);
}
function Cn(e, t) {
	return /* @__PURE__ */ z(e) ? e : new wn(e, t);
}
var wn = class {
	constructor(e, t) {
		this.dep = new vt(), this.__v_isRef = !0, this.__v_isShallow = !1, this._rawValue = t ? e : /* @__PURE__ */ R(e), this._value = t ? e : yn(e), this.__v_isShallow = t;
	}
	get value() {
		return this.dep.track(), this._value;
	}
	set value(e) {
		let t = this._rawValue, n = this.__v_isShallow || /* @__PURE__ */ gn(e) || /* @__PURE__ */ hn(e);
		e = n ? e : /* @__PURE__ */ R(e), P(e, t) && (this._rawValue = e, this._value = n ? e : yn(e), this.dep.trigger());
	}
};
function Tn(e) {
	e.dep && e.dep.trigger();
}
function En(e) {
	return /* @__PURE__ */ z(e) ? e.value : e;
}
function Dn(e) {
	return D(e) ? e() : En(e);
}
var On = {
	get: (e, t, n) => t === "__v_raw" ? e : En(Reflect.get(e, t, n)),
	set: (e, t, n, r) => {
		let i = e[t];
		return /* @__PURE__ */ z(i) && !/* @__PURE__ */ z(n) ? (i.value = n, !0) : Reflect.set(e, t, n, r);
	}
};
function kn(e) {
	return /* @__PURE__ */ mn(e) ? e : new Proxy(e, On);
}
var An = class {
	constructor(e) {
		this.__v_isRef = !0, this._value = void 0;
		let t = this.dep = new vt(), { get: n, set: r } = e(t.track.bind(t), t.trigger.bind(t));
		this._get = n, this._set = r;
	}
	get value() {
		return this._value = this._get();
	}
	set value(e) {
		this._set(e);
	}
};
function jn(e) {
	return new An(e);
}
// @__NO_SIDE_EFFECTS__
function Mn(e) {
	let t = S(e) ? Array(e.length) : {};
	for (let n in e) t[n] = In(e, n);
	return t;
}
var Nn = class {
	constructor(e, t, n) {
		this._object = e, this._defaultValue = n, this.__v_isRef = !0, this._value = void 0, this._key = k(t) ? t : String(t), this._raw = /* @__PURE__ */ R(e);
		let r = !0, i = e;
		if (!S(e) || k(this._key) || !ie(this._key)) do
			r = !/* @__PURE__ */ _n(i) || /* @__PURE__ */ gn(i);
		while (r && (i = i.__v_raw));
		this._shallow = r;
	}
	get value() {
		let e = this._object[this._key];
		return this._shallow && (e = En(e)), this._value = e === void 0 ? this._defaultValue : e;
	}
	set value(e) {
		if (this._shallow && /* @__PURE__ */ z(this._raw[this._key])) {
			let t = this._object[this._key];
			if (/* @__PURE__ */ z(t)) {
				t.value = e;
				return;
			}
		}
		this._object[this._key] = e;
	}
	get dep() {
		return Et(this._raw, this._key);
	}
}, Pn = class {
	constructor(e) {
		this._getter = e, this.__v_isRef = !0, this.__v_isReadonly = !0, this._value = void 0;
	}
	get value() {
		return this._value = this._getter();
	}
};
// @__NO_SIDE_EFFECTS__
function Fn(e, t, n) {
	return /* @__PURE__ */ z(e) ? e : D(e) ? new Pn(e) : A(e) && arguments.length > 1 ? In(e, t, n) : /* @__PURE__ */ xn(e);
}
function In(e, t, n) {
	return new Nn(e, t, n);
}
var Ln = class {
	constructor(e, t, n) {
		this.fn = e, this.setter = t, this._value = void 0, this.dep = new vt(this), this.__v_isRef = !0, this.deps = void 0, this.depsTail = void 0, this.flags = 16, this.globalVersion = gt - 1, this.next = void 0, this.effect = this, this.__v_isReadonly = !t, this.isSSR = n;
	}
	notify() {
		if (this.flags |= 16, !(this.flags & 8) && L !== this) return et(this, !0), !0;
	}
	get value() {
		let e = this.dep.track();
		return ot(this), e && (e.version = this.dep.version), this._value;
	}
	set value(e) {
		this.setter && this.setter(e);
	}
};
// @__NO_SIDE_EFFECTS__
function Rn(e, t, n = !1) {
	let r, i;
	return D(e) ? r = e : (r = e.get, i = e.set), new Ln(r, i, n);
}
var zn = {
	GET: "get",
	HAS: "has",
	ITERATE: "iterate"
}, Bn = {
	SET: "set",
	ADD: "add",
	DELETE: "delete",
	CLEAR: "clear"
}, Vn = {}, Hn = /* @__PURE__ */ new WeakMap(), Un = void 0;
function Wn() {
	return Un;
}
function Gn(e, t = !1, n = Un) {
	if (n) {
		let t = Hn.get(n);
		t || Hn.set(n, t = []), t.push(e);
	}
}
function Kn(e, t, n = f) {
	let { immediate: r, deep: i, once: a, scheduler: o, augmentJob: s, call: c } = n, l = (e) => i ? e : /* @__PURE__ */ gn(e) || i === !1 || i === 0 ? qn(e, 1) : qn(e), u, d, p, h, g = !1, _ = !1;
	if (/* @__PURE__ */ z(e) ? (d = () => e.value, g = /* @__PURE__ */ gn(e)) : /* @__PURE__ */ mn(e) ? (d = () => l(e), g = !0) : S(e) ? (_ = !0, g = e.some((e) => /* @__PURE__ */ mn(e) || /* @__PURE__ */ gn(e)), d = () => e.map((e) => {
		if (/* @__PURE__ */ z(e)) return e.value;
		if (/* @__PURE__ */ mn(e)) return l(e);
		if (D(e)) return c ? c(e, 2) : e();
	})) : d = D(e) ? t ? c ? () => c(e, 2) : e : () => {
		if (p) {
			pt();
			try {
				p();
			} finally {
				mt();
			}
		}
		let t = Un;
		Un = u;
		try {
			return c ? c(e, 3, [h]) : e(h);
		} finally {
			Un = t;
		}
	} : m, t && i) {
		let e = d, t = i === !0 ? Infinity : i;
		d = () => qn(e(), t);
	}
	let v = qe(), b = () => {
		u.stop(), v && v.active && y(v.effects, u);
	};
	if (a && t) {
		let e = t;
		t = (...t) => {
			let n = e(...t);
			return b(), n;
		};
	}
	let x = _ ? Array(e.length).fill(Vn) : Vn, C = (e) => {
		if (u.flags & 1 && (u.dirty || e)) {
			if (t) {
				let n = u.run();
				if (e || i || g || (_ ? n.some((e, t) => P(e, x[t])) : P(n, x))) {
					p && p();
					let e = Un;
					Un = u;
					try {
						let e = [
							n,
							x === Vn ? void 0 : _ && x[0] === Vn ? [] : x,
							h
						];
						x = n, c ? c(t, 3, e) : t(...e);
					} finally {
						Un = e;
					}
				}
			} else u.run();
		}
	};
	return s && s(C), u = new Xe(d), u.scheduler = o ? () => o(C, !1) : C, h = (e) => Gn(e, !1, u), p = u.onStop = () => {
		let e = Hn.get(u);
		if (e) {
			if (c) c(e, 4);
			else for (let t of e) t();
			Hn.delete(u);
		}
	}, t ? r ? C(!0) : x = u.run() : o ? o(C.bind(null, !0), !0) : u.run(), b.pause = u.pause.bind(u), b.resume = u.resume.bind(u), b.stop = b, b;
}
function qn(e, t = Infinity, n) {
	if (t <= 0 || !A(e) || e.__v_skip || (n = n || /* @__PURE__ */ new Map(), (n.get(e) || 0) >= t)) return e;
	if (n.set(e, t), t--, /* @__PURE__ */ z(e)) qn(e.value, t, n);
	else if (S(e)) for (let r = 0; r < e.length; r++) qn(e[r], t, n);
	else if (w(e) || C(e)) e.forEach((e) => {
		qn(e, t, n);
	});
	else if (j(e)) {
		for (let r in e) qn(e[r], t, n);
		for (let r of Object.getOwnPropertySymbols(e)) Object.prototype.propertyIsEnumerable.call(e, r) && qn(e[r], t, n);
	}
	return e;
}
//#endregion
//#region node_modules/@vue/runtime-core/dist/runtime-core.esm-bundler.js
var Jn = [];
function Yn(e) {
	Jn.push(e);
}
function Xn() {
	Jn.pop();
}
var Zn = !1;
function Qn(e, ...t) {
	if (Zn) return;
	Zn = !0, pt();
	let n = Jn.length ? Jn[Jn.length - 1].component : null, r = n && n.appContext.config.warnHandler, i = $n();
	if (r) sr(r, n, 11, [
		e + t.map((e) => e.toString?.call(e) ?? JSON.stringify(e)).join(""),
		n && n.proxy,
		i.map(({ vnode: e }) => `at <${Gc(n, e.type)}>`).join("\n"),
		i
	]);
	else {
		let n = [`[Vue warn]: ${e}`, ...t];
		i.length && n.push("\n", ...er(i)), console.warn(...n);
	}
	mt(), Zn = !1;
}
function $n() {
	let e = Jn[Jn.length - 1];
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
function er(e) {
	let t = [];
	return e.forEach((e, n) => {
		t.push(...n === 0 ? [] : ["\n"], ...tr(e));
	}), t;
}
function tr({ vnode: e, recurseCount: t }) {
	let n = t > 0 ? `... (${t} recursive calls)` : "", r = e.component ? e.component.parent == null : !1, i = ` at <${Gc(e.component, e.type, r)}`, a = ">" + n;
	return e.props ? [
		i,
		...nr(e.props),
		a
	] : [i + a];
}
function nr(e) {
	let t = [], n = Object.keys(e);
	return n.slice(0, 3).forEach((n) => {
		t.push(...rr(n, e[n]));
	}), n.length > 3 && t.push(" ..."), t;
}
function rr(e, t, n) {
	return O(t) ? (t = JSON.stringify(t), n ? t : [`${e}=${t}`]) : typeof t == "number" || typeof t == "boolean" || t == null ? n ? t : [`${e}=${t}`] : /* @__PURE__ */ z(t) ? (t = rr(e, /* @__PURE__ */ R(t.value), !0), n ? t : [
		`${e}=Ref<`,
		t,
		">"
	]) : D(t) ? [`${e}=fn${t.name ? `<${t.name}>` : ""}`] : (t = /* @__PURE__ */ R(t), n ? t : [`${e}=`, t]);
}
function ir(e, t) {}
var ar = {
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
}, or = {
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
function sr(e, t, n, r) {
	try {
		return r ? e(...r) : e();
	} catch (e) {
		lr(e, t, n);
	}
}
function cr(e, t, n, r) {
	if (D(e)) {
		let i = sr(e, t, n, r);
		return i && ee(i) && i.catch((e) => {
			lr(e, t, n);
		}), i;
	}
	if (S(e)) {
		let i = [];
		for (let a = 0; a < e.length; a++) i.push(cr(e[a], t, n, r));
		return i;
	}
}
function lr(e, t, n, r = !0) {
	let i = t ? t.vnode : null, { errorHandler: a, throwUnhandledErrorInProduction: o } = t && t.appContext.config || f;
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
			pt(), sr(a, null, 10, [
				e,
				i,
				o
			]), mt();
			return;
		}
	}
	ur(e, n, i, r, o);
}
function ur(e, t, n, r = !0, i = !1) {
	if (i) throw e;
	console.error(e);
}
var dr = [], fr = -1, pr = [], mr = null, hr = 0, gr = /* @__PURE__ */ Promise.resolve(), _r = null;
function vr(e) {
	let t = _r || gr;
	return e ? t.then(this ? e.bind(this) : e) : t;
}
function yr(e) {
	let t = fr + 1, n = dr.length;
	for (; t < n;) {
		let r = t + n >>> 1, i = dr[r], a = Tr(i);
		a < e || a === e && i.flags & 2 ? t = r + 1 : n = r;
	}
	return t;
}
function br(e) {
	if (!(e.flags & 1)) {
		let t = Tr(e), n = dr[dr.length - 1];
		!n || !(e.flags & 2) && t >= Tr(n) ? dr.push(e) : dr.splice(yr(t), 0, e), e.flags |= 1, xr();
	}
}
function xr() {
	_r || (_r = gr.then(Er));
}
function Sr(e) {
	if (!S(e)) mr && e.id === -1 ? mr.splice(hr + 1, 0, e) : e.flags & 1 || (pr.push(e), e.flags |= 1);
	else for (let t = 0; t < e.length; t++) pr.push(e[t]);
	xr();
}
function Cr(e, t, n = fr + 1) {
	for (; n < dr.length; n++) {
		let t = dr[n];
		if (t && t.flags & 2) {
			if (e && t.id !== e.uid) continue;
			dr.splice(n, 1), n--, t.flags & 4 && (t.flags &= -2), t(), t.flags & 4 || (t.flags &= -2);
		}
	}
}
function wr(e) {
	if (pr.length) {
		let e = [...new Set(pr)].sort((e, t) => Tr(e) - Tr(t));
		if (pr.length = 0, mr) {
			for (let t = 0; t < e.length; t++) mr.push(e[t]);
			return;
		}
		for (mr = e, hr = 0; hr < mr.length; hr++) {
			let e = mr[hr];
			e.flags & 4 && (e.flags &= -2), e.flags & 8 || e(), e.flags &= -2;
		}
		mr = null, hr = 0;
	}
}
var Tr = (e) => e.id == null ? e.flags & 2 ? -1 : Infinity : e.id;
function Er(e) {
	try {
		for (fr = 0; fr < dr.length; fr++) {
			let e = dr[fr];
			e && !(e.flags & 8) && (e.flags & 4 && (e.flags &= -2), sr(e, e.i, e.i ? 15 : 14), e.flags & 4 || (e.flags &= -2));
		}
	} finally {
		for (; fr < dr.length; fr++) {
			let e = dr[fr];
			e && (e.flags &= -2);
		}
		fr = -1, dr.length = 0, wr(e), _r = null, (dr.length || pr.length) && Er(e);
	}
}
var Dr, Or = [], kr = !1;
function Ar(e, ...t) {
	Dr ? Dr.emit(e, ...t) : kr || Or.push({
		event: e,
		args: t
	});
}
function jr(e, t) {
	Dr = e, Dr ? (Dr.enabled = !0, Or.forEach(({ event: e, args: t }) => Dr.emit(e, ...t)), Or = []) : typeof window < "u" && window.HTMLElement && !(window.navigator?.userAgent)?.includes("jsdom") ? ((t.__VUE_DEVTOOLS_HOOK_REPLAY__ = t.__VUE_DEVTOOLS_HOOK_REPLAY__ || []).push((e) => {
		jr(e, t);
	}), setTimeout(() => {
		Dr || (t.__VUE_DEVTOOLS_HOOK_REPLAY__ = null, kr = !0, Or = []);
	}, 3e3)) : (kr = !0, Or = []);
}
function Mr(e, t) {
	Ar("app:init", e, t, {
		Fragment: H,
		Text: Ks,
		Comment: U,
		Static: qs
	});
}
function Nr(e) {
	Ar("app:unmount", e);
}
var Pr = /* @__PURE__ */ Rr("component:added"), Fr = /* @__PURE__ */ Rr("component:updated"), Ir = /* @__PURE__ */ Rr("component:removed"), Lr = (e) => {
	Dr && typeof Dr.cleanupBuffer == "function" && !Dr.cleanupBuffer(e) && Ir(e);
};
// @__NO_SIDE_EFFECTS__
function Rr(e) {
	return (t) => {
		Ar(e, t.appContext.app, t.uid, t.parent ? t.parent.uid : void 0, t);
	};
}
function zr(e, t, n) {
	Ar("component:emit", e.appContext.app, e, t, n);
}
var B = null, Br = null;
function Vr(e) {
	let t = B;
	return B = e, Br = e && e.type.__scopeId || null, t;
}
function Hr(e) {
	Br = e;
}
function Ur() {
	Br = null;
}
var Wr = (e) => Gr;
function Gr(e, t = B, n) {
	if (!t || e._n) return e;
	let r = (...n) => {
		r._d && $s(-1);
		let i = Vr(t), a = Js.length, o;
		try {
			o = e(...n);
		} finally {
			for (let e = Js.length; e > a; e--) Zs();
			Vr(i), r._d && $s(1);
		}
		return __VUE_PROD_DEVTOOLS__ && Fr(t), o;
	};
	return r._n = !0, r._c = !0, r._d = !0, r;
}
function Kr(e, t) {
	if (B === null) return e;
	let n = Vc(B), r = e.dirs || (e.dirs = []);
	for (let e = 0; e < t.length; e++) {
		let [i, a, o, s = f] = t[e];
		i && (D(i) && (i = {
			mounted: i,
			updated: i
		}), i.deep && qn(a), r.push({
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
function qr(e, t, n, r) {
	let i = e.dirs, a = t && t.dirs;
	for (let o = 0; o < i.length; o++) {
		let s = i[o];
		a && (s.oldValue = a[o].value);
		let c = s.dir[r];
		c && (pt(), cr(c, n, 8, [
			e.el,
			s,
			e,
			t
		]), mt());
	}
}
function Jr(e, t) {
	if (Cc) {
		let n = Cc.provides, r = Cc.parent && Cc.parent.provides;
		r === n && (n = Cc.provides = Object.create(r)), n[e] = t;
	}
}
function Yr(e, t, n = !1) {
	let r = wc();
	if (r || Ho) {
		let i = Ho ? Ho._context.provides : r ? r.parent == null || r.ce ? r.vnode.appContext && r.vnode.appContext.provides : r.parent.provides : void 0;
		if (i && e in i) return i[e];
		if (arguments.length > 1) return n && D(t) ? t.call(r && r.proxy) : t;
	}
}
function Xr() {
	return !!(wc() || Ho);
}
var Zr = /* @__PURE__ */ Symbol.for("v-scx"), Qr = () => Yr(Zr);
function $r(e, t) {
	return ri(e, null, t);
}
function ei(e, t) {
	return ri(e, null, { flush: "post" });
}
function ti(e, t) {
	return ri(e, null, { flush: "sync" });
}
function ni(e, t, n) {
	return ri(e, t, n);
}
function ri(e, t, n = f) {
	let { immediate: r, deep: i, flush: a, once: o } = n, s = v({}, n), c = t && r || !t && a !== "post", l;
	if (Ac) {
		if (a === "sync") {
			let e = Qr();
			l = e.__watcherHandles || (e.__watcherHandles = []);
		} else if (!c) {
			let e = () => {};
			return e.stop = m, e.resume = m, e.pause = m, e;
		}
	}
	let u = Cc;
	s.call = (e, t, n) => cr(e, u, t, n);
	let d = !1;
	a === "post" ? s.scheduler = (e) => {
		V(e, u && u.suspense);
	} : a !== "sync" && (d = !0, s.scheduler = (e, t) => {
		t ? e() : br(e);
	}), s.augmentJob = (e) => {
		t && (e.flags |= 4), d && (e.flags |= 2, u && (e.id = u.uid, e.i = u));
	};
	let p = Kn(e, t, s);
	return Ac && (l ? l.push(p) : c && p()), p;
}
function ii(e, t, n) {
	let r = this.proxy, i = O(e) ? e.includes(".") ? ai(r, e) : () => r[e] : e.bind(r, r), a;
	D(t) ? a = t : (a = t.handler, n = t);
	let o = Dc(this), s = ri(i, a.bind(r), n);
	return o(), s;
}
function ai(e, t) {
	let n = t.split(".");
	return () => {
		let t = e;
		for (let e = 0; e < n.length && t; e++) t = t[n[e]];
		return t;
	};
}
var oi = /* @__PURE__ */ new WeakMap(), si = /* @__PURE__ */ Symbol("_vte"), ci = (e) => e.__isTeleport, li = (e) => e && (e.disabled || e.disabled === ""), ui = (e) => e && (e.defer || e.defer === ""), di = (e) => typeof SVGElement < "u" && e instanceof SVGElement, fi = (e) => typeof MathMLElement == "function" && e instanceof MathMLElement, pi = (e, t) => {
	let n = e && e.to;
	return O(n) ? t ? t(n) : null : n;
}, mi = {
	name: "Teleport",
	__isTeleport: !0,
	process(e, t, n, r, i, a, o, s, c, l) {
		let { mc: u, pc: d, pbc: f, o: { insert: p, querySelector: m, createText: h, createComment: g, parentNode: _ } } = l, v = li(t.props), { dynamicChildren: y } = t, b = (e, t, n) => {
			e.shapeFlag & 16 && u(e.children, t, n, i, a, o, s, c);
		}, x = (e = t) => {
			let n = li(e.props), r = e.target = pi(e.props, m), a = yi(r, e, h, p);
			r && (o !== "svg" && di(r) ? o = "svg" : o !== "mathml" && fi(r) && (o = "mathml"), i && i.isCE && (i.ce._teleportTargets || (i.ce._teleportTargets = /* @__PURE__ */ new Set())).add(r), n || (b(e, r, a), vi(e, !1)));
		}, S = (e) => {
			let t = () => {
				if (oi.get(e) === t) {
					if (oi.delete(e), li(e.props)) {
						let t = _(e.el) || n;
						b(e, t, e.anchor), vi(e, !0);
					}
					x(e);
				}
			};
			oi.set(e, t), V(t, a);
		};
		if (e == null) {
			let e = t.el = h(""), i = t.anchor = h("");
			if (p(e, n, r), p(i, n, r), ui(t.props) || a && a.pendingBranch) {
				S(t);
				return;
			}
			v && (b(t, n, i), vi(t, !0)), x();
		} else {
			t.el = e.el;
			let r = t.anchor = e.anchor, u = oi.get(e);
			if (u) {
				u.flags |= 8, oi.delete(e), S(t);
				return;
			}
			t.targetStart = e.targetStart;
			let p = t.target = e.target, h = t.targetAnchor = e.targetAnchor, g = li(e.props), _ = g ? n : p, b = g ? r : h;
			if (o === "svg" || di(p) ? o = "svg" : (o === "mathml" || fi(p)) && (o = "mathml"), y ? (f(e.dynamicChildren, y, _, i, a, o, s), Os(e, t, !0)) : c || d(e, t, _, b, i, a, o, s, !1), v) g ? t.props && e.props && t.props.to !== e.props.to && (t.props.to = e.props.to) : hi(t, n, r, l, 1);
			else if ((t.props && t.props.to) !== (e.props && e.props.to)) {
				let e = pi(t.props, m);
				e && (t.target = e, hi(t, e, null, l, 0));
			} else g && hi(t, p, h, l, 1);
			vi(t, v);
		}
	},
	remove(e, t, n, { um: r, o: { remove: i } }, a) {
		let { shapeFlag: o, children: s, anchor: c, targetStart: l, targetAnchor: u, target: d, props: f } = e, p = li(f), m = a || !p, h = oi.get(e);
		if (h && (h.flags |= 8, oi.delete(e)), d && (i(l), i(u)), a && i(c), !h && (p || d) && o & 16) for (let e = 0; e < s.length; e++) {
			let i = s[e];
			r(i, t, n, m, !!i.dynamicChildren);
		}
	},
	move: hi,
	hydrate: gi
};
function hi(e, t, n, { o: { insert: r }, m: i }, a = 2) {
	a === 0 && r(e.targetAnchor, t, n);
	let { el: o, anchor: s, shapeFlag: c, children: l, props: u } = e, d = a === 2;
	if (d && r(o, t, n), !oi.has(e) && (!d || li(u)) && c & 16) for (let e = 0; e < l.length; e++) i(l[e], t, n, 2);
	d && r(s, t, n);
}
function gi(e, t, n, r, i, a, { o: { nextSibling: o, parentNode: s, querySelector: c, insert: l, createText: u } }, d) {
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
	let m = t.target = pi(t.props, c), h = li(t.props);
	if (m) {
		let c = m._lpa || m.firstChild;
		t.shapeFlag & 16 && (h ? (p(e, t), f(m, c), t.targetAnchor || yi(m, t, u, l, s(e) === m ? e : null)) : (t.anchor = o(e), f(m, c), t.targetAnchor || yi(m, t, u, l), d(c && o(c), t, m, n, r, i, a))), vi(t, h);
	} else h && t.shapeFlag & 16 && (p(e, t), t.targetStart = e, t.targetAnchor = o(e));
	return t.anchor && o(t.anchor);
}
var _i = mi;
function vi(e, t) {
	let n = e.ctx;
	if (n && n.ut) {
		let r, i;
		for (t ? (r = e.el, i = e.anchor) : (r = e.targetStart, i = e.targetAnchor); r && r !== i;) r.nodeType === 1 && r.setAttribute("data-v-owner", n.uid), r = r.nextSibling;
		n.ut();
	}
}
function yi(e, t, n, r, i = null) {
	let a = t.targetStart = n(""), o = t.targetAnchor = n("");
	return a[si] = o, e && (r(a, e, i), r(o, e, i)), o;
}
var bi = /* @__PURE__ */ Symbol("_leaveCb"), xi = /* @__PURE__ */ Symbol("_enterCb");
function Si() {
	let e = {
		isMounted: !1,
		isLeaving: !1,
		isUnmounting: !1,
		leavingVNodes: /* @__PURE__ */ new Map()
	};
	return Fa(() => {
		e.isMounted = !0;
	}), Ra(() => {
		e.isUnmounting = !0;
	}), e;
}
var Ci = [Function, Array], wi = {
	mode: String,
	appear: Boolean,
	persisted: Boolean,
	onBeforeEnter: Ci,
	onEnter: Ci,
	onAfterEnter: Ci,
	onEnterCancelled: Ci,
	onBeforeLeave: Ci,
	onLeave: Ci,
	onAfterLeave: Ci,
	onLeaveCancelled: Ci,
	onBeforeAppear: Ci,
	onAppear: Ci,
	onAfterAppear: Ci,
	onAppearCancelled: Ci
}, Ti = (e) => {
	let t = e.subTree;
	return t.component ? Ti(t.component) : t;
}, Ei = {
	name: "BaseTransition",
	props: wi,
	setup(e, { slots: t }) {
		let n = wc(), r = Si();
		return () => {
			let i = t.default && Pi(t.default(), !0), a = i && i.length ? Di(i) : n.subTree ? mc() : void 0;
			if (!a) return;
			let o = /* @__PURE__ */ R(e), { mode: s } = o;
			if (r.isLeaving) return ji(a);
			let c = Mi(a);
			if (!c) return ji(a);
			let l = Ai(c, o, r, n, (e) => l = e);
			c.type !== U && Ni(c, l);
			let u = n.subTree && Mi(n.subTree);
			if (u && u.type !== U && !ic(u, c) && Ti(n).type !== U) {
				let e = Ai(u, o, r, n);
				if (Ni(u, e), s === "out-in" && c.type !== U) return r.isLeaving = !0, e.afterLeave = () => {
					r.isLeaving = !1, n.job.flags & 8 || n.update(), delete e.afterLeave, u = void 0;
				}, ji(a);
				s === "in-out" && c.type !== U ? e.delayLeave = (e, t, n) => {
					let i = ki(r, u);
					i[String(u.key)] = u, e[bi] = () => {
						t(), e[bi] = void 0, delete l.delayedLeave, u = void 0;
					}, l.delayedLeave = () => {
						n(), delete l.delayedLeave, u = void 0;
					};
				} : u = void 0;
			} else u && (u = void 0);
			return a;
		};
	}
};
function Di(e) {
	let t = e[0];
	if (e.length > 1) {
		for (let n of e) if (n.type !== U) {
			t = n;
			break;
		}
	}
	return t;
}
var Oi = Ei;
function ki(e, t) {
	let { leavingVNodes: n } = e, r = n.get(t.type);
	return r || (r = /* @__PURE__ */ Object.create(null), n.set(t.type, r)), r;
}
function Ai(e, t, n, r, i) {
	let { appear: a, mode: o, persisted: s = !1, onBeforeEnter: c, onEnter: l, onAfterEnter: u, onEnterCancelled: d, onBeforeLeave: f, onLeave: p, onAfterLeave: m, onLeaveCancelled: h, onBeforeAppear: g, onAppear: _, onAfterAppear: v, onAppearCancelled: y } = t, b = String(e.key), x = ki(n, e), C = (e, t) => {
		e && cr(e, r, 9, t);
	}, w = (e, t) => {
		let n = t[1];
		C(e, t), S(e) ? e.every((e) => e.length <= 1) && n() : e.length <= 1 && n();
	}, T = {
		mode: o,
		persisted: s,
		beforeEnter(t) {
			let r = c;
			if (!n.isMounted) {
				if (a) r = g || c;
				else return;
			}
			t[bi] && t[bi](!0);
			let i = x[b];
			i && ic(e, i) && i.el[bi] && i.el[bi](), C(r, [t]);
		},
		enter(t) {
			if (x[b] === e) return;
			let r = l, i = u, o = d;
			if (!n.isMounted) {
				if (a) r = _ || l, i = v || u, o = y || d;
				else return;
			}
			let s = !1;
			t[xi] = (e) => {
				s || (s = !0, C(e ? o : i, [t]), T.delayedLeave && T.delayedLeave(), t[xi] = void 0);
			};
			let c = t[xi].bind(null, !1);
			r ? w(r, [t, c]) : c();
		},
		leave(t, r) {
			let i = String(e.key);
			if (t[xi] && t[xi](!0), n.isUnmounting) return r();
			C(f, [t]);
			let a = !1;
			t[bi] = (n) => {
				a || (a = !0, r(), C(n ? h : m, [t]), t[bi] = void 0, x[i] === e && delete x[i]);
			};
			let o = t[bi].bind(null, !1);
			x[i] = e, p ? w(p, [t, o]) : o();
		},
		clone(e) {
			let a = Ai(e, t, n, r, i);
			return i && i(a), a;
		}
	};
	return T;
}
function ji(e) {
	if (Ca(e)) return e = dc(e), e.children = null, e;
}
function Mi(e) {
	if (!Ca(e)) return ci(e.type) && e.children ? Di(e.children) : e;
	if (e.component) return e.component.subTree;
	let { shapeFlag: t, children: n } = e;
	if (n) {
		if (t & 16) return n[0];
		if (t & 32 && D(n.default)) return n.default();
	}
}
function Ni(e, t) {
	if (e.shapeFlag & 6 && e.component) {
		e.transition = t;
		let n = e.component.subTree;
		Ni(ci(n.type) && Mi(n) || n, t);
	} else e.shapeFlag & 128 ? (e.ssContent.transition = t.clone(e.ssContent), e.ssFallback.transition = t.clone(e.ssFallback)) : e.transition = t;
}
function Pi(e, t = !1, n) {
	let r = [], i = 0;
	for (let a = 0; a < e.length; a++) {
		let o = e[a], s = n == null ? o.key : String(n) + String(o.key == null ? a : o.key);
		o.type === H ? (o.patchFlag & 128 && i++, r = r.concat(Pi(o.children, t, s))) : (t || o.type !== U) && r.push(s == null ? o : dc(o, { key: s }));
	}
	if (i > 1) for (let e = 0; e < r.length; e++) r[e].patchFlag = -2;
	return r;
}
// @__NO_SIDE_EFFECTS__
function Fi(e, t) {
	return D(e) ? /* @__PURE__ */ v({ name: e.name }, t, { setup: e }) : e;
}
function Ii() {
	let e = wc();
	return e ? (e.appContext.config.idPrefix || "v") + "-" + e.ids[0] + e.ids[1]++ : "";
}
function Li(e) {
	e.ids = [
		e.ids[0] + e.ids[2]++ + "-",
		0,
		0
	];
}
function Ri(e) {
	let t = wc(), n = /* @__PURE__ */ Sn(null);
	if (t) {
		let r = t.refs === f ? t.refs = {} : t.refs;
		Object.defineProperty(r, e, {
			enumerable: !0,
			get: () => n.value,
			set: (e) => n.value = e
		});
	}
	return n;
}
function zi(e, t) {
	let n;
	return !!((n = Object.getOwnPropertyDescriptor(e, t)) && !n.configurable);
}
var Bi = /* @__PURE__ */ new WeakMap();
function Vi(e, t, n, r, i = !1) {
	if (S(e)) {
		e.forEach((e, a) => Vi(e, t && (S(t) ? t[a] : t), n, r, i));
		return;
	}
	if (ba(r) && !i) {
		r.shapeFlag & 512 && r.type.__asyncResolved && r.component.subTree.component && Vi(e, t, n, r.component.subTree);
		return;
	}
	let a = r.shapeFlag & 4 ? Vc(r.component) : r.el, o = i ? null : a, { i: s, r: c } = e, l = t && t.r, u = s.refs === f ? s.refs = {} : s.refs, d = s.setupState, p = /* @__PURE__ */ R(d), m = d === f ? h : (e) => !zi(u, e) && x(p, e), g = (e, t) => !(t && zi(u, t));
	if (l != null && l !== c) {
		if (Hi(t), O(l)) u[l] = null, m(l) && (d[l] = null);
		else if (/* @__PURE__ */ z(l)) {
			let e = t;
			g(l, e.k) && (l.value = null), e.k && (u[e.k] = null);
		}
	}
	if (D(c)) sr(c, s, 12, [o, u]);
	else {
		let t = O(c), r = /* @__PURE__ */ z(c);
		if (t || r) {
			let s = () => {
				if (e.f) {
					let n = t ? m(c) ? d[c] : u[c] : g(c) || !e.k ? c.value : u[e.k];
					if (i) S(n) && y(n, a);
					else if (S(n)) n.includes(a) || n.push(a);
					else if (t) u[c] = [a], m(c) && (d[c] = u[c]);
					else {
						let t = [a];
						g(c, e.k) && (c.value = t), e.k && (u[e.k] = t);
					}
				} else t ? (u[c] = o, m(c) && (d[c] = o)) : r && (g(c, e.k) && (c.value = o), e.k && (u[e.k] = o));
			};
			if (o) {
				let t = () => {
					s(), Bi.delete(e);
				};
				t.id = -1, Bi.set(e, t), V(t, n);
			} else Hi(e), s();
		}
	}
}
function Hi(e) {
	let t = Bi.get(e);
	t && (t.flags |= 8, Bi.delete(e));
}
var Ui = !1, Wi = () => {
	Ui || (console.error("Hydration completed but contains mismatches."), Ui = !0);
}, Gi = (e) => e.namespaceURI.includes("svg") && e.tagName !== "foreignObject", Ki = (e) => e.namespaceURI.includes("MathML"), qi = (e) => {
	if (e.nodeType === 1) {
		if (Gi(e)) return "svg";
		if (Ki(e)) return "mathml";
	}
}, Ji = (e) => e.nodeType === 8;
function Yi(e) {
	let { mt: t, p: n, o: { patchProp: r, createText: i, nextSibling: a, parentNode: o, remove: s, insert: c, createComment: l } } = e, u = (e, t) => {
		t.hasChildNodes() ? (d(t.firstChild, e, null, null, null), wr(), t._vnode = e) : (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Attempting to hydrate existing markup but container is empty. Performing full mount instead."), n(null, e, t), wr(), t._vnode = e);
	}, d = (n, r, s, l, u, g = !1) => {
		g = g || !!r.dynamicChildren;
		let b = Ji(n) && n.data === "[", x = () => h(n, r, s, l, u, b), { type: S, ref: C, shapeFlag: w, patchFlag: T } = r, E = n.nodeType;
		r.el = n, __VUE_PROD_DEVTOOLS__ && (fe(n, "__vnode", r, !0), fe(n, "__vueParentComponent", s, !0)), T === -2 && (g = !1, r.dynamicChildren = null);
		let D = null;
		switch (S) {
			case Ks:
				E === 3 ? (n.data !== r.children && (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Hydration text mismatch in", n.parentNode, `
  - rendered on server: ${JSON.stringify(n.data)}
  - expected on client: ${JSON.stringify(r.children)}`), Wi(), n.data = r.children), D = a(n)) : r.children === "" ? (c(r.el = i(""), o(n), n), D = n) : D = x();
				break;
			case U:
				y(n) ? (D = a(n), v(r.el = n.content.firstChild, n, s)) : D = E !== 8 || b ? x() : a(n);
				break;
			case qs:
				if (b && (n = a(n), E = n.nodeType), E === 1 || E === 3) {
					D = n;
					let e = !r.children.length;
					for (let t = 0; t < r.staticCount; t++) e && (r.children += D.nodeType === 1 ? D.outerHTML : D.data), t === r.staticCount - 1 && (r.anchor = D), D = a(D);
					return b ? a(D) : D;
				}
				x();
				break;
			case H:
				D = b ? m(n, r, s, l, u, g) : x();
				break;
			default: if (w & 1) D = (E !== 1 || r.type.toLowerCase() !== n.tagName.toLowerCase()) && !y(n) ? x() : f(n, r, s, l, u, g);
			else if (w & 6) {
				r.slotScopeIds = u;
				let e = o(n);
				if (D = b ? _(n) : Ji(n) && n.data === "teleport start" ? _(n, n.data, "teleport end") : a(n), t(r, e, null, s, l, qi(e), g), (ba(r) || r.component.asyncDep) && !r.component.subTree) {
					let t;
					b ? (t = W(qs), t.anchor = D ? D.previousSibling : e.lastChild) : t = n.nodeType === 3 ? fc("") : W(n.nodeType === 8 ? U : "div"), t.el = n, r.component.subTree = t;
				}
			} else w & 64 ? D = E === 8 ? r.type.hydrate(n, r, s, l, u, g, e, p) : x() : w & 128 ? D = r.type.hydrate(n, r, s, l, qi(o(n)), u, g, e, d) : __VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Invalid HostVNode type:", S, `(${typeof S})`);
		}
		return C != null && Vi(C, null, l, r), D;
	}, f = (e, t, n, i, a, o) => {
		o = o || !!t.dynamicChildren;
		let { type: c, dynamicProps: l, props: u, patchFlag: d, shapeFlag: f, dirs: m, transition: h } = t, _ = c === "input" || c === "option", b = !!l;
		if (_ || b || d !== -1) {
			m && qr(t, null, n, "created");
			let c = !1;
			if (y(e)) {
				c = Ds(null, h) && n && n.vnode.props && n.vnode.props.appear;
				let r = e.content.firstChild;
				if (c) {
					let e = r.getAttribute("class");
					e && (r.$cls = e), h.beforeEnter(r);
				}
				v(r, e, n), t.el = e = r;
			}
			if (f & 16 && !(u && (u.innerHTML || u.textContent))) {
				let r = p(e.firstChild, t, e, n, i, a, o);
				for (r && !sa(e, 1) && (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Hydration children mismatch on", e, "\nServer rendered element contains more child nodes than client vdom."), Wi()); r;) {
					let e = r;
					r = r.nextSibling, s(e);
				}
			} else if (f & 8) {
				let n = t.children;
				n[0] === "\n" && (e.tagName === "PRE" || e.tagName === "TEXTAREA") && (n = n.slice(1));
				let { textContent: r } = e;
				r !== n && r !== n.replace(/\r\n|\r/g, "\n") && (sa(e, 0) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Hydration text content mismatch on", e, `
  - rendered on server: ${r}
  - expected on client: ${n}`), Wi()), e.textContent = t.children);
			}
			if (u) {
				if (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ || _ || b || !o || d & 48) {
					let i = e.tagName.includes("-"), a = e.namespaceURI.includes("svg") ? "svg" : e.namespaceURI.includes("MathML") ? "mathml" : void 0;
					for (let o in u) if (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && !(m && m.some((e) => e.dir.created)) && Qi(e, o, u[o], t, n) && Wi(), _ && (o.endsWith("value") || o === "indeterminate") || g(o) && !ae(o) || o[0] === "." || i && !ae(o) || l && l.includes(o)) {
						if (Zi(e, o, u[o])) continue;
						r(e, o, null, u[o], a, n);
					}
				} else if (u.onClick) r(e, "onClick", null, u.onClick, void 0, n);
				else if (d & 4 && /* @__PURE__ */ mn(u.style)) for (let e in u.style) u.style[e];
			}
			let x;
			(x = u && u.onVnodeBeforeMount) && yc(x, n, t), m && qr(t, null, n, "beforeMount"), ((x = u && u.onVnodeMounted) || m || c) && Us(() => {
				x && yc(x, n, t), c && h.enter(e), m && qr(t, null, n, "mounted");
			}, i);
		}
		return e.nextSibling;
	}, p = (e, t, r, o, s, l, u) => {
		u = u || !!t.dynamicChildren;
		let f = t.children, p = f.length, m = !1;
		for (let t = 0; t < p; t++) {
			let h = u ? f[t] : f[t] = hc(f[t]), g = h.type === Ks;
			e ? (g && !u && t + 1 < p && hc(f[t + 1]).type === Ks && (c(i(e.data.slice(h.children.length)), r, a(e)), e.data = h.children), e = d(e, h, o, s, l, u)) : g && !h.children ? c(h.el = i(""), r) : (m || (m = !0, sa(r, 1) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Hydration children mismatch on", r, "\nServer rendered element contains fewer child nodes than client vdom."), Wi())), n(null, h, r, null, o, s, qi(r), l));
		}
		return e;
	}, m = (e, t, n, r, i, s) => {
		let { slotScopeIds: u } = t;
		u && (i = i ? i.concat(u) : u);
		let d = o(e), f = p(a(e), t, d, n, r, i, s);
		return f && Ji(f) && f.data === "]" ? a(t.anchor = f) : (Wi(), c(t.anchor = l("]"), d, f), f);
	}, h = (e, t, r, i, c, l) => {
		if (la(e, t) || (__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ && Qn("Hydration node mismatch:\n- rendered on server:", e, e.nodeType === 3 ? "(text)" : Ji(e) && e.data === "[" ? "(start of fragment)" : "", "\n- expected on client:", t.type), Wi()), t.el = null, l) {
			let t = _(e);
			for (;;) {
				let n = a(e);
				if (n && n !== t) s(n);
				else break;
			}
		}
		let u = a(e), d = o(e);
		return s(e), n(null, t, d, u, r, i, qi(d), c), r && (r.vnode.el = t.el, ns(r, t.el)), u;
	}, _ = (e, t = "[", n = "]") => {
		let r = 0;
		for (; e;) if (e = a(e), e && Ji(e) && (e.data === t && r++, e.data === n)) {
			if (r === 0) return a(e);
			r--;
		}
		return e;
	}, v = (e, t, n) => {
		let r = t.parentNode;
		r && r.replaceChild(e, t);
		let i = n;
		for (; i;) i.vnode.el === t && (i.vnode.el = i.subTree.el = e), i = i.parent;
	}, y = (e) => e.nodeType === 1 && e.tagName === "TEMPLATE";
	return [u, d];
}
var Xi = /* @__PURE__ */ new Set([
	"src",
	"srcset",
	"href",
	"poster"
]);
function Zi(e, t, n) {
	return Xi.has(t) ? e.getAttribute(t) === (n == null ? null : `${n}`) : !1;
}
function Qi(e, t, n, r, i) {
	let a, o, s, c;
	if (t === "class") e.$cls ? (s = e.$cls, delete e.$cls) : s = e.getAttribute("class"), c = Ce(n), ta(ea(s || ""), ea(c)) || (a = 2, o = "class");
	else if (t === "style") {
		s = e.getAttribute("style") || "", c = O(n) ? n : Se(ve(n));
		let t = na(s), l = na(c);
		if (r.dirs) for (let { dir: e, value: t } of r.dirs) e.name === "show" && !t && l.set("display", "none");
		i && ia(i, r, l), ra(t, l) || (a = 3, o = "style");
	} else (e instanceof SVGElement && Ae(t) || e instanceof HTMLElement && (De(t) || ke(t))) && (t === "hidden" ? (s = $i(e.getAttribute(t)), c = $i(n)) : De(t) ? (s = e.hasAttribute(t), c = Oe(n)) : n == null ? (s = e.hasAttribute(t), c = !1) : (s = e.hasAttribute(t) ? e.getAttribute(t) : t === "value" && e.tagName === "TEXTAREA" && e.value, c = je(n) ? String(n) : !1), s !== c && (a = 4, o = t));
	if (a != null && !sa(e, a)) {
		let t = (e) => e === !1 ? "(not rendered)" : `${o}="${e}"`;
		return Qn(`Hydration ${oa[a]} mismatch on`, e, `
  - rendered on server: ${t(s)}
  - expected on client: ${t(c)}
  Note: this mismatch is check-only. The DOM will not be rectified in production due to performance overhead.
  You should fix the source of the mismatch.`), !0;
	}
	return !1;
}
function $i(e) {
	return je(e) ? O(e) ? e.toLowerCase() === "until-found" ? "until-found" : "" : Oe(e) ? "" : !1 : !1;
}
function ea(e) {
	return new Set(e.trim().split(/\s+/));
}
function ta(e, t) {
	if (e.size !== t.size) return !1;
	for (let n of e) if (!t.has(n)) return !1;
	return !0;
}
function na(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e.split(";")) {
		let [e, r] = n.split(":");
		e = e.trim(), r = r && r.trim(), e && r && t.set(e, r);
	}
	return t;
}
function ra(e, t) {
	if (e.size !== t.size) return !1;
	for (let [n, r] of e) if (r !== t.get(n)) return !1;
	return !0;
}
function ia(e, t, n) {
	let r = e.subTree;
	if (e.getCssVars && (t === r || r && r.type === H && r.children.includes(t))) {
		let t = e.getCssVars();
		for (let e in t) {
			let r = We(t[e]);
			n.set(`--${Ne(e, !1)}`, r);
		}
	}
	t === r && e.parent && ia(e.parent, e.vnode, n);
}
var aa = "data-allow-mismatch", oa = {
	0: "text",
	1: "children",
	2: "class",
	3: "style",
	4: "attribute"
};
function sa(e, t) {
	if (t === 0 || t === 1) for (; e && !e.hasAttribute(aa);) e = e.parentElement;
	return ca(e && e.getAttribute(aa), t);
}
function ca(e, t) {
	if (e == null) return !1;
	if (e === "") return !0;
	{
		let n = e.split(",");
		return t === 0 && n.includes("children") ? !0 : n.includes(oa[t]);
	}
}
function la(e, t) {
	return sa(e.parentElement, 1) || ua(e) || da(t);
}
function ua(e) {
	return e.nodeType === 1 && ca(e.getAttribute(aa), 1);
}
function da({ props: e }) {
	let t = e && e[aa];
	return typeof t == "string" && ca(t, 1);
}
var fa = ge().requestIdleCallback || ((e) => setTimeout(e, 1)), pa = ge().cancelIdleCallback || ((e) => clearTimeout(e)), ma = (e = 1e4) => (t) => {
	let n = fa(t, { timeout: e });
	return () => pa(n);
};
function ha(e) {
	let { top: t, left: n, bottom: r, right: i } = e.getBoundingClientRect(), { innerHeight: a, innerWidth: o } = window;
	return (t > 0 && t < a || r > 0 && r < a) && (n > 0 && n < o || i > 0 && i < o);
}
var ga = (e) => (t, n) => {
	let r = new IntersectionObserver((e) => {
		for (let n of e) if (n.isIntersecting) {
			r.disconnect(), t();
			break;
		}
	}, e);
	return n((e) => {
		if (e instanceof Element) {
			if (ha(e)) return t(), r.disconnect(), !1;
			r.observe(e);
		}
	}), () => r.disconnect();
}, _a = (e) => (t) => {
	if (e) {
		let n = matchMedia(e);
		if (n.matches) t();
		else return n.addEventListener("change", t, { once: !0 }), () => n.removeEventListener("change", t);
	}
}, va = (e = []) => (t, n) => {
	O(e) && (e = [e]);
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
function ya(e, t) {
	if (Ji(e) && e.data === "[") {
		let n = 1, r = e.nextSibling;
		for (; r;) {
			if (r.nodeType === 1) {
				if (t(r) === !1) break;
			} else if (Ji(r)) {
				if (r.data === "]") {
					if (--n === 0) break;
				} else r.data === "[" && n++;
			}
			r = r.nextSibling;
		}
	} else t(e);
}
var ba = (e) => !!e.type.__asyncLoader;
// @__NO_SIDE_EFFECTS__
function xa(e) {
	D(e) && (e = { loader: e });
	let { loader: t, loadingComponent: n, errorComponent: r, delay: i = 200, hydrate: a, timeout: o, suspensible: s = !0, onError: c } = e, l = null, u, d = 0, f = () => (d++, l = null, p()), p = () => {
		let e;
		return l || (e = l = t().catch((e) => {
			if (e = e instanceof Error ? e : Error(String(e)), c) return new Promise((t, n) => {
				c(e, () => t(f()), () => n(e), d + 1);
			});
			throw e;
		}).then((t) => e !== l && l ? l : (t && (t.__esModule || t[Symbol.toStringTag] === "Module") && (t = t.default), u = t, t)));
	};
	return /* @__PURE__ */ Fi({
		name: "AsyncComponentWrapper",
		__asyncLoader: p,
		__asyncHydrate(e, t, n) {
			let r = e.isConnected, i = !1;
			(t.bu || (t.bu = [])).push(() => i = !0);
			let o = () => {
				i || !e.parentNode || r && !e.isConnected || n();
			}, s = a ? () => {
				let n = a(o, (t) => ya(e, t));
				n && (t.bum || (t.bum = [])).push(n);
			} : o;
			u ? s() : p().then(() => !t.isUnmounted && s());
		},
		get __asyncResolved() {
			return u;
		},
		setup() {
			let e = Cc;
			if (Li(e), u) return () => Sa(u, e);
			let t = (t) => {
				l = null, lr(t, e, 13, !r);
			};
			if (s && e.suspense || Ac) return p().then((t) => () => Sa(t, e)).catch((e) => (t(e), () => r ? W(r, { error: e }) : null));
			let a = /* @__PURE__ */ xn(!1), c = /* @__PURE__ */ xn(), d = /* @__PURE__ */ xn(!!i), f, m;
			return za(() => {
				f != null && clearTimeout(f), m != null && clearTimeout(m);
			}), i && (m = setTimeout(() => {
				e.isUnmounted || (d.value = !1);
			}, i)), o != null && (f = setTimeout(() => {
				if (!e.isUnmounted && !a.value && !c.value) {
					let e = /* @__PURE__ */ Error(`Async component timed out after ${o}ms.`);
					t(e), c.value = e;
				}
			}, o)), p().then(() => {
				e.isUnmounted || (a.value = !0, e.parent && Ca(e.parent.vnode) && e.parent.update());
			}).catch((n) => {
				e.isUnmounted ? l = null : (t(n), c.value = n);
			}), () => {
				if (a.value && u) return Sa(u, e);
				if (c.value && r) return W(r, { error: c.value });
				if (n && !d.value) return Sa(n, e);
			};
		}
	});
}
function Sa(e, t) {
	let { ref: n, props: r, children: i, ce: a } = t.vnode, o = W(e, r, i);
	return o.ref = n, o.ce = a, delete t.vnode.ce, o;
}
var Ca = (e) => e.type.__isKeepAlive, wa = {
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
		let n = wc(), r = n.ctx;
		if (!r.renderer) return () => {
			let e = t.default && t.default();
			return e && e.length === 1 ? e[0] : e;
		};
		let i = /* @__PURE__ */ new Map(), a = /* @__PURE__ */ new Set(), o = null;
		__VUE_PROD_DEVTOOLS__ && (n.__v_cache = i);
		let s = n.suspense, { renderer: { p: c, m: l, um: u, o: { createElement: d } } } = r, f = d("div");
		r.activate = (e, t, n, r, i) => {
			let a = e.component;
			l(e, t, n, 0, s), c(a.vnode, e, t, n, a, s, r, e.slotScopeIds, i), V(() => {
				a.isDeactivated = !1, a.a && de(a.a);
				let t = e.props && e.props.onVnodeMounted;
				t && yc(t, a.parent, e);
			}, s), __VUE_PROD_DEVTOOLS__ && Pr(a);
		}, r.deactivate = (e) => {
			let t = e.component;
			js(t.m), js(t.a), l(e, f, null, 1, s), V(() => {
				t.da && de(t.da);
				let n = e.props && e.props.onVnodeUnmounted;
				n && yc(n, t.parent, e), t.isDeactivated = !0;
			}, s), __VUE_PROD_DEVTOOLS__ && Pr(t);
		};
		function p(e) {
			Aa(e), u(e, n, s, !0);
		}
		function m(e) {
			i.forEach((t, n) => {
				let r = Wc(ba(t) ? t.type.__asyncResolved || {} : t.type);
				r && !e(r) && h(n);
			});
		}
		function h(e) {
			let t = i.get(e);
			t && (!o || !ic(t, o)) ? p(t) : o && Aa(o), i.delete(e), a.delete(e);
		}
		ni(() => [e.include, e.exclude], ([e, t]) => {
			e && m((t) => Ta(e, t)), t && m((e) => !Ta(t, e));
		}, {
			flush: "post",
			deep: !0
		});
		let g = null, _ = () => {
			g != null && (Ns(n.subTree.type) ? V(() => {
				let e = ja(n.subTree);
				e.component && i.set(g, e);
			}, n.subTree.suspense) : i.set(g, ja(n.subTree)));
		};
		return Fa(_), La(_), Ra(() => {
			i.forEach((e) => {
				let { subTree: t, suspense: r } = n, i = ja(t);
				if (e.type === i.type && e.key === i.key) {
					Aa(i);
					let e = i.component.da;
					e && V(e, r);
				} else p(e);
			});
		}), () => {
			if (g = null, !t.default) return o = null;
			let n = t.default(), r = n[0];
			if (n.length > 1) return o = null, n;
			if (!rc(r) || !(r.shapeFlag & 4) && !(r.shapeFlag & 128)) return o = null, r;
			let s = ja(r);
			if (s.type === U) return o = null, s;
			let c = s.type, l = Wc(ba(s) ? s.type.__asyncResolved || {} : c), { include: u, exclude: d, max: f } = e;
			if (u && (!l || !Ta(u, l)) || d && l && Ta(d, l)) return s.shapeFlag &= -257, o = s, r;
			let p = s.key == null ? c : s.key, m = i.get(p);
			return s.el && (s = dc(s), r.shapeFlag & 128 && (r.ssContent = s)), g = p, m ? (s.el = m.el, s.component = m.component, s.transition && Ni(s, s.transition), s.shapeFlag |= 512, a.delete(p), a.add(p)) : (a.add(p), f && a.size > parseInt(f, 10) && h(a.values().next().value)), s.shapeFlag |= 256, o = s, Ns(r.type) ? r : s;
		};
	}
};
function Ta(e, t) {
	return S(e) ? e.some((e) => Ta(e, t)) : O(e) ? e.split(",").includes(t) : E(e) ? (e.lastIndex = 0, e.test(t)) : !1;
}
function Ea(e, t) {
	Oa(e, "a", t);
}
function Da(e, t) {
	Oa(e, "da", t);
}
function Oa(e, t, n = Cc) {
	let r = e.__wdc || (e.__wdc = () => {
		let t = n;
		for (; t;) {
			if (t.isDeactivated) return;
			t = t.parent;
		}
		return e();
	});
	if (Ma(t, r, n), n) {
		let e = n.parent;
		for (; e && e.parent;) Ca(e.parent.vnode) && ka(r, t, n, e), e = e.parent;
	}
}
function ka(e, t, n, r) {
	let i = Ma(t, e, r, !0);
	za(() => {
		y(r[t], i);
	}, n);
}
function Aa(e) {
	e.shapeFlag &= -257, e.shapeFlag &= -513;
}
function ja(e) {
	return e.shapeFlag & 128 ? e.ssContent : e;
}
function Ma(e, t, n = Cc, r = !1) {
	if (n) {
		let i = n[e] || (n[e] = []), a = t.__weh || (t.__weh = (...r) => {
			pt();
			let i = Dc(n), a = cr(t, n, e, r);
			return i(), mt(), a;
		});
		return r ? i.unshift(a) : i.push(a), a;
	}
}
var Na = (e) => (t, n = Cc) => {
	(!Ac || e === "sp") && Ma(e, (...e) => t(...e), n);
}, Pa = Na("bm"), Fa = Na("m"), Ia = Na("bu"), La = Na("u"), Ra = Na("bum"), za = Na("um"), Ba = Na("sp"), Va = Na("rtg"), Ha = Na("rtc");
function Ua(e, t = Cc) {
	Ma("ec", e, t);
}
var Wa = "components", Ga = "directives";
function Ka(e, t) {
	return Xa(Wa, e, !0, t) || e;
}
var qa = /* @__PURE__ */ Symbol.for("v-ndc");
function Ja(e) {
	return O(e) ? Xa(Wa, e, !1) || e : e || qa;
}
function Ya(e) {
	return Xa(Ga, e);
}
function Xa(e, t, n = !0, r = !1) {
	let i = B || Cc;
	if (i) {
		let n = i.type;
		if (e === Wa) {
			let e = Wc(n, !1);
			if (e && (e === t || e === M(t) || e === le(M(t)))) return n;
		}
		let a = Za(i[e] || n[e], t) || Za(i.appContext[e], t);
		return !a && r ? n : a;
	}
}
function Za(e, t) {
	return e && (e[t] || e[M(t)] || e[le(M(t))]);
}
function Qa(e, t, n, r) {
	let i, a = n && n[r], o = S(e);
	if (o || O(e)) {
		let n = o && /* @__PURE__ */ mn(e), r = !1, s = !1;
		n && (r = !/* @__PURE__ */ gn(e), s = /* @__PURE__ */ hn(e), e = Ot(e)), i = Array(e.length);
		for (let n = 0, o = e.length; n < o; n++) i[n] = t(r ? s ? bn(yn(e[n])) : yn(e[n]) : e[n], n, void 0, a && a[n]);
	} else if (typeof e == "number") {
		i = Array(e);
		for (let n = 0; n < e; n++) i[n] = t(n + 1, n, void 0, a && a[n]);
	} else if (A(e)) {
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
function $a(e, t) {
	for (let n = 0; n < t.length; n++) {
		let r = t[n];
		if (S(r)) for (let t = 0; t < r.length; t++) e[r[t].name] = r[t].fn;
		else r && (e[r.name] = r.key ? (...e) => {
			let t = r.fn(...e);
			return t && (t.key = r.key), t;
		} : r.fn);
	}
	return e;
}
function eo(e, t, n, r, i, a) {
	if (n ?? (n = {}), B.ce || B.parent && ba(B.parent) && B.parent.ce) {
		let e = a != null && n.key == null ? v({}, n, { key: a }) : n, i = Object.keys(e).length > 0;
		return t !== "default" && (e.name = t), Xs(), nc(H, null, [W("slot", e, r && r())], i ? -2 : 64);
	}
	let o = e[t];
	o && o._c && (o._d = !1);
	let s = Js.length;
	Xs();
	let c;
	try {
		let i = o && to(o(n)), s = n.key || a || i && i.key;
		c = nc(H, { key: (s && !k(s) ? s : `_${t}`) + (!i && r ? "_fb" : "") }, i || (r ? r() : []), i && e._ === 1 ? 64 : -2);
	} catch (e) {
		for (let e = Js.length; e > s; e--) Zs();
		throw e;
	} finally {
		o && o._c && (o._d = !0);
	}
	return !i && c.scopeId && (c.slotScopeIds = [c.scopeId + "-s"]), c;
}
function to(e) {
	return e.some((e) => !rc(e) || !(e.type === U || e.type === H && !to(e.children))) ? e : null;
}
function no(e, t) {
	let n = {};
	for (let r in e) n[t && /[A-Z]/.test(r) ? `on:${r}` : ue(r)] = e[r];
	return n;
}
var ro = (e) => e ? kc(e) ? Vc(e) : ro(e.parent) : null, io = /* @__PURE__ */ v(/* @__PURE__ */ Object.create(null), {
	$: (e) => e,
	$el: (e) => e.vnode.el,
	$data: (e) => e.data,
	$props: (e) => e.props,
	$attrs: (e) => e.attrs,
	$slots: (e) => e.slots,
	$refs: (e) => e.refs,
	$parent: (e) => ro(e.parent),
	$root: (e) => ro(e.root),
	$host: (e) => e.ce,
	$emit: (e) => e.emit,
	$options: (e) => __VUE_OPTIONS_API__ ? ko(e) : e.type,
	$forceUpdate: (e) => e.f || (e.f = () => {
		br(e.update);
	}),
	$nextTick: (e) => e.n || (e.n = vr.bind(e.proxy)),
	$watch: (e) => __VUE_OPTIONS_API__ ? ii.bind(e) : m
}), ao = (e, t) => e !== f && !e.__isScriptSetup && x(e, t), oo = {
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
			else if (ao(r, t)) return o[t] = 1, r[t];
			else if (__VUE_OPTIONS_API__ && i !== f && x(i, t)) return o[t] = 2, i[t];
			else if (x(a, t)) return o[t] = 3, a[t];
			else if (n !== f && x(n, t)) return o[t] = 4, n[t];
			else (!__VUE_OPTIONS_API__ || wo) && (o[t] = 0);
		}
		let l = io[t], u, d;
		if (l) return t === "$attrs" && wt(e.attrs, "get", ""), l(e);
		if ((u = s.__cssModules) && (u = u[t])) return u;
		if (n !== f && x(n, t)) return o[t] = 4, n[t];
		if (d = c.config.globalProperties, x(d, t)) return d[t];
	},
	set({ _: e }, t, n) {
		let { data: r, setupState: i, ctx: a } = e;
		return ao(i, t) ? (i[t] = n, !0) : __VUE_OPTIONS_API__ && r !== f && x(r, t) ? (r[t] = n, !0) : x(e.props, t) || t[0] === "$" && t.slice(1) in e ? !1 : (a[t] = n, !0);
	},
	has({ _: { data: e, setupState: t, accessCache: n, ctx: r, appContext: i, props: a, type: o } }, s) {
		let c;
		return !!(n[s] || __VUE_OPTIONS_API__ && e !== f && s[0] !== "$" && x(e, s) || ao(t, s) || x(a, s) || x(r, s) || x(io, s) || x(i.config.globalProperties, s) || (c = o.__cssModules) && c[s]);
	},
	defineProperty(e, t, n) {
		return n.get == null ? x(n, "value") && this.set(e, t, n.value, null) : e._.accessCache[t] = 0, Reflect.defineProperty(e, t, n);
	}
}, so = /* @__PURE__ */ v({}, oo, {
	get(e, t) {
		if (t !== Symbol.unscopables) return oo.get(e, t, e);
	},
	has(e, t) {
		return t[0] !== "_" && !_e(t);
	}
});
function co() {
	return null;
}
function lo() {
	return null;
}
function uo(e) {}
function fo(e) {}
function po() {
	return null;
}
function mo() {}
function ho(e, t) {
	return null;
}
function go() {
	return vo("useSlots").slots;
}
function _o() {
	return vo("useAttrs").attrs;
}
function vo(e) {
	let t = wc();
	return t.setupContext || (t.setupContext = Bc(t));
}
function yo(e) {
	return S(e) ? e.reduce((e, t) => (e[t] = null, e), {}) : e;
}
function bo(e, t) {
	let n = yo(e);
	for (let e in t) {
		if (e.startsWith("__skip")) continue;
		let r = n[e];
		r ? S(r) || D(r) ? r = n[e] = {
			type: r,
			default: t[e]
		} : r.default = t[e] : r === null && (r = n[e] = { default: t[e] }), r && t[`__skip_${e}`] && (r.skipFactory = !0);
	}
	return n;
}
function xo(e, t) {
	return !e || !t ? e || t : S(e) && S(t) ? e.concat(t) : v({}, yo(e), yo(t));
}
function So(e, t) {
	let n = {};
	for (let r in e) t.includes(r) || Object.defineProperty(n, r, {
		enumerable: !0,
		get: () => e[r]
	});
	return n;
}
function Co(e) {
	let t = wc(), n = Ac, r = e();
	Oc(), n && Ec(!1);
	let i = () => {
		Dc(t), n && Ec(!0);
	}, a = () => {
		wc() !== t && t.scope.off(), Oc(), n && Ec(!1);
	};
	return ee(r) && (r = r.catch((e) => {
		throw i(), Promise.resolve().then(() => Promise.resolve().then(a)), e;
	})), [r, () => {
		i(), Promise.resolve().then(a);
	}];
}
var wo = !0;
function To(e) {
	let t = ko(e), n = e.proxy, r = e.ctx;
	wo = !1, t.beforeCreate && Do(t.beforeCreate, e, "bc");
	let { data: i, computed: a, methods: o, watch: s, provide: c, inject: l, created: u, beforeMount: d, mounted: f, beforeUpdate: p, updated: h, activated: g, deactivated: _, beforeDestroy: v, beforeUnmount: y, destroyed: b, unmounted: x, render: C, renderTracked: w, renderTriggered: T, errorCaptured: E, serverPrefetch: O, expose: k, inheritAttrs: ee, components: te, directives: ne, filters: re } = t;
	if (l && Eo(l, r, null), o) for (let e in o) {
		let t = o[e];
		D(t) && (r[e] = t.bind(n));
	}
	if (i) {
		let t = i.call(n, n);
		A(t) && (e.data = /* @__PURE__ */ ln(t));
	}
	if (wo = !0, a) for (let e in a) {
		let t = a[e], i = qc({
			get: D(t) ? t.bind(n, n) : D(t.get) ? t.get.bind(n, n) : m,
			set: !D(t) && D(t.set) ? t.set.bind(n) : m
		});
		Object.defineProperty(r, e, {
			enumerable: !0,
			configurable: !0,
			get: () => i.value,
			set: (e) => i.value = e
		});
	}
	if (s) for (let e in s) Oo(s[e], r, n, e);
	if (c) {
		let e = D(c) ? c.call(n) : c;
		Reflect.ownKeys(e).forEach((t) => {
			Jr(t, e[t]);
		});
	}
	u && Do(u, e, "c");
	function j(e, t) {
		S(t) ? t.forEach((t) => e(t.bind(n))) : t && e(t.bind(n));
	}
	if (j(Pa, d), j(Fa, f), j(Ia, p), j(La, h), j(Ea, g), j(Da, _), j(Ua, E), j(Ha, w), j(Va, T), j(Ra, y), j(za, x), j(Ba, O), S(k)) {
		if (k.length) {
			let t = e.exposed || (e.exposed = {});
			k.forEach((e) => {
				Object.defineProperty(t, e, {
					get: () => n[e],
					set: (t) => n[e] = t,
					enumerable: !0
				});
			});
		} else e.exposed || (e.exposed = {});
	}
	C && e.render === m && (e.render = C), ee != null && (e.inheritAttrs = ee), te && (e.components = te), ne && (e.directives = ne), O && Li(e);
}
function Eo(e, t, n = m) {
	S(e) && (e = Po(e));
	for (let n in e) {
		let r = e[n], i;
		i = A(r) ? "default" in r ? Yr(r.from || n, r.default, !0) : Yr(r.from || n) : Yr(r), /* @__PURE__ */ z(i) ? Object.defineProperty(t, n, {
			enumerable: !0,
			configurable: !0,
			get: () => i.value,
			set: (e) => i.value = e
		}) : t[n] = i;
	}
}
function Do(e, t, n) {
	cr(S(e) ? e.map((e) => e.bind(t.proxy)) : e.bind(t.proxy), t, n);
}
function Oo(e, t, n, r) {
	let i = r.includes(".") ? ai(n, r) : () => n[r];
	if (O(e)) {
		let n = t[e];
		D(n) && ni(i, n);
	} else if (D(e)) ni(i, e.bind(n));
	else if (A(e)) {
		if (S(e)) e.forEach((e) => Oo(e, t, n, r));
		else {
			let r = D(e.handler) ? e.handler.bind(n) : t[e.handler];
			D(r) && ni(i, r, e);
		}
	}
}
function ko(e) {
	let t = e.type, { mixins: n, extends: r } = t, { mixins: i, optionsCache: a, config: { optionMergeStrategies: o } } = e.appContext, s = a.get(t), c;
	return s ? c = s : !i.length && !n && !r ? c = t : (c = {}, i.length && i.forEach((e) => Ao(c, e, o, !0)), Ao(c, t, o)), A(t) && a.set(t, c), c;
}
function Ao(e, t, n, r = !1) {
	let { mixins: i, extends: a } = t;
	a && Ao(e, a, n, !0), i && i.forEach((t) => Ao(e, t, n, !0));
	for (let i in t) if (!(r && i === "expose")) {
		let r = jo[i] || n && n[i];
		e[i] = r ? r(e[i], t[i]) : t[i];
	}
	return e;
}
var jo = {
	data: Mo,
	props: Lo,
	emits: Lo,
	methods: Io,
	computed: Io,
	beforeCreate: Fo,
	created: Fo,
	beforeMount: Fo,
	mounted: Fo,
	beforeUpdate: Fo,
	updated: Fo,
	beforeDestroy: Fo,
	beforeUnmount: Fo,
	destroyed: Fo,
	unmounted: Fo,
	activated: Fo,
	deactivated: Fo,
	errorCaptured: Fo,
	serverPrefetch: Fo,
	components: Io,
	directives: Io,
	watch: Ro,
	provide: Mo,
	inject: No
};
function Mo(e, t) {
	return t ? e ? function() {
		return v(D(e) ? e.call(this, this) : e, D(t) ? t.call(this, this) : t);
	} : t : e;
}
function No(e, t) {
	return Io(Po(e), Po(t));
}
function Po(e) {
	if (S(e)) {
		let t = {};
		for (let n = 0; n < e.length; n++) t[e[n]] = e[n];
		return t;
	}
	return e;
}
function Fo(e, t) {
	return e ? [...new Set([].concat(e, t))] : t;
}
function Io(e, t) {
	return e ? v(/* @__PURE__ */ Object.create(null), e, t) : t;
}
function Lo(e, t) {
	return e ? S(e) && S(t) ? [.../* @__PURE__ */ new Set([...e, ...t])] : v(/* @__PURE__ */ Object.create(null), yo(e), yo(t ?? {})) : t;
}
function Ro(e, t) {
	if (!e) return t;
	if (!t) return e;
	let n = v(/* @__PURE__ */ Object.create(null), e);
	for (let r in t) n[r] = Fo(e[r], t[r]);
	return n;
}
function zo() {
	return {
		app: null,
		config: {
			isNativeTag: h,
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
var Bo = 0;
function Vo(e, t) {
	return function(n, r = null) {
		D(n) || (n = v({}, n)), r != null && !A(r) && (r = null);
		let i = zo(), a = /* @__PURE__ */ new WeakSet(), o = [], s = !1, c = i.app = {
			_uid: Bo++,
			_component: n,
			_props: r,
			_container: null,
			_context: i,
			_instance: null,
			version: Qc,
			get config() {
				return i.config;
			},
			set config(e) {},
			use(e, ...t) {
				return a.has(e) || (e && D(e.install) ? (a.add(e), e.install(c, ...t)) : D(e) && (a.add(e), e(c, ...t))), c;
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
					let u = c._ceVNode || W(n, r);
					return u.appContext = i, l === !0 ? l = "svg" : l === !1 && (l = void 0), o && t ? t(u, a) : e(u, a, l), s = !0, c._container = a, a.__vue_app__ = c, __VUE_PROD_DEVTOOLS__ && (c._instance = u.component, Mr(c, Qc)), Vc(u.component);
				}
			},
			onUnmount(e) {
				o.push(e);
			},
			unmount() {
				s && (cr(o, c._instance, 16), e(null, c._container), __VUE_PROD_DEVTOOLS__ && (c._instance = null, Nr(c)), delete c._container.__vue_app__);
			},
			provide(e, t) {
				return i.provides[e] = t, c;
			},
			runWithContext(e) {
				let t = Ho;
				Ho = c;
				try {
					return e();
				} finally {
					Ho = t;
				}
			}
		};
		return c;
	};
}
var Ho = null;
function Uo(e, t, n = f) {
	let r = wc(), i = M(t), a = N(t), o = Wo(e, i), s = jn((o, s) => {
		let c, l = f, u;
		return ti(() => {
			let t = e[i];
			P(c, t) && (c = t, s());
		}), {
			get() {
				return o(), n.get ? n.get(c) : c;
			},
			set(e) {
				let o = n.set ? n.set(e) : e;
				if (!P(o, c) && !(l !== f && P(e, l))) return;
				let d = r.vnode.props, p = !!(d && (t in d || i in d || a in d) && (`onUpdate:${t}` in d || `onUpdate:${i}` in d || `onUpdate:${a}` in d));
				p || (c = e, s()), r.emit(`update:${t}`, o), P(e, l) && (P(e, o) && !P(o, u) || p && l !== f && !P(o, c)) && s(), l = e, u = o;
			}
		};
	});
	return s[Symbol.iterator] = () => {
		let e = 0;
		return { next() {
			return e < 2 ? {
				value: e++ ? o || f : s,
				done: !1
			} : { done: !0 };
		} };
	}, s;
}
var Wo = (e, t) => t === "modelValue" || t === "model-value" ? e.modelModifiers : e[`${t}Modifiers`] || e[`${M(t)}Modifiers`] || e[`${N(t)}Modifiers`];
function Go(e, t, ...n) {
	if (e.isUnmounted) return;
	let r = e.vnode.props || f, i = n, a = t.startsWith("update:"), o = a && Wo(r, t.slice(7));
	o && (o.trim && (i = n.map((e) => O(e) ? e.trim() : e)), o.number && (i = i.map(pe))), __VUE_PROD_DEVTOOLS__ && zr(e, t, i);
	let s, c = r[s = ue(t)] || r[s = ue(M(t))];
	!c && a && (c = r[s = ue(N(t))]), c && cr(c, e, 6, i);
	let l = r[s + "Once"];
	if (l) {
		if (!e.emitted) e.emitted = {};
		else if (e.emitted[s]) return;
		e.emitted[s] = !0, cr(l, e, 6, i);
	}
}
var Ko = /* @__PURE__ */ new WeakMap();
function qo(e, t, n = !1) {
	let r = __VUE_OPTIONS_API__ && n ? Ko : t.emitsCache, i = r.get(e);
	if (i !== void 0) return i;
	let a = e.emits, o = {}, s = !1;
	if (__VUE_OPTIONS_API__ && !D(e)) {
		let r = (e) => {
			let n = qo(e, t, !0);
			n && (s = !0, v(o, n));
		};
		!n && t.mixins.length && t.mixins.forEach(r), e.extends && r(e.extends), e.mixins && e.mixins.forEach(r);
	}
	return !a && !s ? (A(e) && r.set(e, null), null) : (S(a) ? a.forEach((e) => o[e] = null) : v(o, a), A(e) && r.set(e, o), o);
}
function Jo(e, t) {
	return !e || !g(t) ? !1 : (t = t.slice(2), t = t === "Once" ? t : t.replace(/Once$/, ""), x(e, t[0].toLowerCase() + t.slice(1)) || x(e, N(t)) || x(e, t));
}
function Yo(e) {
	let { type: t, vnode: n, proxy: r, withProxy: i, propsOptions: [a], slots: o, attrs: s, emit: c, render: l, renderCache: u, props: d, data: f, setupState: p, ctx: m, inheritAttrs: h } = e, g = Vr(e), v, y;
	try {
		if (n.shapeFlag & 4) {
			let e = i || r, t = e;
			v = hc(l.call(t, e, u, d, p, f, m)), y = s;
		} else {
			let e = t;
			v = hc(e.length > 1 ? e(d, {
				attrs: s,
				slots: o,
				emit: c
			}) : e(d, null)), y = t.props ? s : Zo(s);
		}
	} catch (t) {
		Js.length = 0, lr(t, e, 1), v = W(U);
	}
	let b = v;
	if (y && h !== !1) {
		let e = Object.keys(y), { shapeFlag: t } = b;
		e.length && t & 7 && (a && e.some(_) && (y = Qo(y, a)), b = dc(b, y, !1, !0));
	}
	return n.dirs && (b = dc(b, null, !1, !0), b.dirs = b.dirs ? b.dirs.concat(n.dirs) : n.dirs), n.transition && Ni(ci(b.type) && Mi(b) || b, n.transition), v = b, Vr(g), v;
}
function Xo(e, t = !0) {
	let n;
	for (let t = 0; t < e.length; t++) {
		let r = e[t];
		if (rc(r)) {
			if (r.type !== U || r.children === "v-if") {
				if (n) return;
				n = r;
			}
		} else return;
	}
	return n;
}
var Zo = (e) => {
	let t;
	for (let n in e) (n === "class" || n === "style" || g(n)) && ((t || (t = {}))[n] = e[n]);
	return t;
}, Qo = (e, t) => {
	let n = {};
	for (let r in e) (!_(r) || !(r.slice(9) in t)) && (n[r] = e[r]);
	return n;
};
function $o(e, t, n) {
	let { props: r, children: i, component: a } = e, { props: o, children: s, patchFlag: c } = t, l = a.emitsOptions;
	if (t.dirs || t.transition) return !0;
	if (n && c >= 0) {
		if (c & 1024) return !0;
		if (c & 16) return r ? es(r, o, l) : !!o;
		if (c & 8) {
			let e = t.dynamicProps;
			for (let t = 0; t < e.length; t++) {
				let n = e[t];
				if (ts(o, r, n) && !Jo(l, n)) return !0;
			}
		}
	} else return (i || s) && (!s || !s.$stable) ? !0 : r === o ? !1 : r ? !o || es(r, o, l) : !!o;
	return !1;
}
function es(e, t, n) {
	let r = Object.keys(t);
	if (r.length !== Object.keys(e).length) return !0;
	for (let i = 0; i < r.length; i++) {
		let a = r[i];
		if (ts(t, e, a) && !Jo(n, a)) return !0;
	}
	return !1;
}
function ts(e, t, n) {
	let r = e[n], i = t[n];
	return n === "style" && A(r) && A(i) ? !Re(r, i) : r !== i;
}
function ns({ vnode: e, parent: t, suspense: n }, r) {
	for (; t;) {
		let n = t.subTree;
		if (n.suspense && n.suspense.activeBranch === e && (n.suspense.vnode.el = n.el = r, e = n), n === e) (e = t.vnode).el = r, t = t.parent;
		else break;
	}
	n && n.activeBranch === e && (n.vnode.el = r);
}
var rs = {}, is = () => Object.create(rs), as = (e) => Object.getPrototypeOf(e) === rs;
function os(e, t, n, r = !1) {
	let i = {}, a = is();
	e.propsDefaults = /* @__PURE__ */ Object.create(null), cs(e, t, i, a);
	for (let t in e.propsOptions[0]) t in i || (i[t] = void 0);
	e.props = n ? r ? i : /* @__PURE__ */ un(i) : e.type.props ? i : a, e.attrs = a;
}
function ss(e, t, n, r) {
	let { props: i, attrs: a, vnode: { patchFlag: o } } = e, s = /* @__PURE__ */ R(i), [c] = e.propsOptions, l = !1;
	if ((r || o > 0) && !(o & 16)) {
		if (o & 8) {
			let n = e.vnode.dynamicProps;
			for (let r = 0; r < n.length; r++) {
				let o = n[r];
				if (Jo(e.emitsOptions, o)) continue;
				let u = t[o];
				if (c) {
					if (x(a, o)) u !== a[o] && (a[o] = u, l = !0);
					else {
						let t = M(o);
						i[t] = ls(c, s, t, u, e, !1);
					}
				} else u !== a[o] && (a[o] = u, l = !0);
			}
		}
	} else {
		cs(e, t, i, a) && (l = !0);
		let r;
		for (let a in s) (!t || !x(t, a) && ((r = N(a)) === a || !x(t, r))) && (c ? n && (n[a] !== void 0 || n[r] !== void 0) && (i[a] = ls(c, s, a, void 0, e, !0)) : delete i[a]);
		if (a !== s) for (let e in a) (!t || !x(t, e)) && (delete a[e], l = !0);
	}
	l && Tt(e.attrs, "set", "");
}
function cs(e, t, n, r) {
	let [i, a] = e.propsOptions, o = !1, s;
	if (t) for (let c in t) {
		if (ae(c)) continue;
		let l = t[c], u;
		i && x(i, u = M(c)) ? !a || !a.includes(u) ? n[u] = l : (s || (s = {}))[u] = l : Jo(e.emitsOptions, c) || (!(c in r) || l !== r[c]) && (r[c] = l, o = !0);
	}
	if (a) {
		let t = /* @__PURE__ */ R(n), r = s || f;
		for (let o = 0; o < a.length; o++) {
			let s = a[o];
			n[s] = ls(i, t, s, r[s], e, !x(r, s));
		}
	}
	return o;
}
function ls(e, t, n, r, i, a) {
	let o = e[n];
	if (o != null) {
		let e = x(o, "default");
		if (e && r === void 0) {
			let e = o.default;
			if (o.type !== Function && !o.skipFactory && D(e)) {
				let { propsDefaults: a } = i;
				if (n in a) r = a[n];
				else {
					let o = Dc(i);
					r = a[n] = e.call(null, t), o();
				}
			} else r = e;
			i.ce && i.ce._setProp(n, r);
		}
		o[0] && (a && !e ? r = !1 : o[1] && (r === "" || r === N(n)) && (r = !0));
	}
	return r;
}
var us = /* @__PURE__ */ new WeakMap();
function ds(e, t, n = !1) {
	let r = __VUE_OPTIONS_API__ && n ? us : t.propsCache, i = r.get(e);
	if (i) return i;
	let a = e.props, o = {}, s = [], c = !1;
	if (__VUE_OPTIONS_API__ && !D(e)) {
		let r = (e) => {
			c = !0;
			let [n, r] = ds(e, t, !0);
			v(o, n), r && s.push(...r);
		};
		!n && t.mixins.length && t.mixins.forEach(r), e.extends && r(e.extends), e.mixins && e.mixins.forEach(r);
	}
	if (!a && !c) return A(e) && r.set(e, p), p;
	if (S(a)) for (let e = 0; e < a.length; e++) {
		let t = M(a[e]);
		fs(t) && (o[t] = f);
	}
	else if (a) for (let e in a) {
		let t = M(e);
		if (fs(t)) {
			let n = a[e], r = o[t] = S(n) || D(n) ? { type: n } : v({}, n), i = r.type, c = !1, l = !0;
			if (S(i)) for (let e = 0; e < i.length; ++e) {
				let t = i[e], n = D(t) && t.name;
				if (n === "Boolean") {
					c = !0;
					break;
				}
				n === "String" && (l = !1);
			}
			else c = D(i) && i.name === "Boolean";
			r[0] = c, r[1] = l, (c || x(r, "default")) && s.push(t);
		}
	}
	let l = [o, s];
	return A(e) && r.set(e, l), l;
}
function fs(e) {
	return e[0] !== "$" && !ae(e);
}
var ps = (e) => e === "_" || e === "_ctx" || e === "$stable", ms = (e) => S(e) ? e.map(hc) : [hc(e)], hs = (e, t, n) => {
	if (t._n) return t;
	let r = Gr((...e) => ms(t(...e)), n);
	return r._c = !1, r;
}, gs = (e, t, n) => {
	let r = e._ctx;
	for (let n in e) {
		if (ps(n)) continue;
		let i = e[n];
		if (D(i)) t[n] = hs(n, i, r);
		else if (i != null) {
			let e = ms(i);
			t[n] = () => e;
		}
	}
}, _s = (e, t) => {
	let n = ms(t);
	e.slots.default = () => n;
}, vs = (e, t, n) => {
	for (let r in t) (n || !ps(r)) && (e[r] = t[r]);
}, ys = (e, t, n) => {
	let r = e.slots = is();
	if (e.vnode.shapeFlag & 32) {
		let e = t._;
		e ? (vs(r, t, n), n && fe(r, "_", e, !0)) : gs(t, r);
	} else t && _s(e, t);
}, bs = (e, t, n) => {
	let { vnode: r, slots: i } = e, a = !0, o = f;
	if (r.shapeFlag & 32) {
		let e = t._;
		e ? n && e === 1 ? a = !1 : vs(i, t, n) : (a = !t.$stable, gs(t, i)), o = t;
	} else t && (_s(e, t), o = { default: 1 });
	if (a) for (let e in i) !ps(e) && o[e] == null && delete i[e];
};
function xs() {
	typeof __VUE_OPTIONS_API__ != "boolean" && (ge().__VUE_OPTIONS_API__ = !0), typeof __VUE_PROD_DEVTOOLS__ != "boolean" && (ge().__VUE_PROD_DEVTOOLS__ = !1), typeof __VUE_PROD_HYDRATION_MISMATCH_DETAILS__ != "boolean" && (ge().__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ = !1);
}
var V = Us;
function Ss(e) {
	return ws(e);
}
function Cs(e) {
	return ws(e, Yi);
}
function ws(e, t) {
	xs();
	let n = ge();
	n.__VUE__ = !0, __VUE_PROD_DEVTOOLS__ && jr(n.__VUE_DEVTOOLS_GLOBAL_HOOK__, n);
	let { insert: r, remove: i, patchProp: a, createElement: o, createText: s, createComment: c, setText: l, setElementText: u, parentNode: d, nextSibling: h, setScopeId: g = m, insertStaticContent: _ } = e, v = (e, t, n, r = null, i = null, a = null, o = void 0, s = null, c = !!t.dynamicChildren) => {
		if (e === t) return;
		e && !ic(e, t) && (r = me(e), N(e, i, a, !0), e = null), t.patchFlag === -2 && (c = !1, t.dynamicChildren = null), t.dynamicChildren && e && e.dynamicChildren && e.dynamicChildren.hasOnce && (t.dynamicChildren === p && (t.dynamicChildren = []), t.dynamicChildren.hasOnce = !0);
		let { type: l, ref: u, shapeFlag: d } = t;
		switch (l) {
			case Ks:
				y(e, t, n, r);
				break;
			case U:
				b(e, t, n, r);
				break;
			case qs:
				e ?? x(t, n, r, o);
				break;
			case H:
				ee(e, t, n, r, i, a, o, s, c);
				break;
			default: d & 1 ? w(e, t, n, r, i, a, o, s, c) : d & 6 ? te(e, t, n, r, i, a, o, s, c) : (d & 64 || d & 128) && l.process(e, t, n, r, i, a, o, s, c, ve);
		}
		u != null && i ? Vi(u, e && e.ref, a, t || e, !t) : u == null && e && e.ref != null && Vi(e.ref, null, a, e, !0);
	}, y = (e, t, n, i) => {
		if (e == null) r(t.el = s(t.children), n, i);
		else {
			let n = t.el = e.el;
			t.children !== e.children && l(n, t.children);
		}
	}, b = (e, t, n, i) => {
		e == null ? r(t.el = c(t.children || ""), n, i) : t.el = e.el;
	}, x = (e, t, n, r) => {
		[e.el, e.anchor] = _(e.children, t, n, r, e.el, e.anchor);
	}, S = ({ el: e, anchor: t }, n, i) => {
		let a;
		for (; e && e !== t;) a = h(e), r(e, n, i), e = a;
		r(t, n, i);
	}, C = ({ el: e, anchor: t }) => {
		let n;
		for (; e && e !== t;) n = h(e), i(e), e = n;
		i(t);
	}, w = (e, t, n, r, i, a, o, s, c) => {
		if (t.type === "svg" ? o = "svg" : t.type === "math" && (o = "mathml"), e == null) T(t, n, r, i, a, o, s, c);
		else {
			let n = e.el && e.el._isVueCE ? e.el : null;
			try {
				n && n._beginPatch(), O(e, t, i, a, o, s, c);
			} finally {
				n && n._endPatch();
			}
		}
	}, T = (e, t, n, i, s, c, l, d) => {
		let f, p, { props: m, shapeFlag: h, transition: g, dirs: _ } = e;
		if (f = e.el = o(e.type, c, m && m.is, m), h & 8 ? u(f, e.children) : h & 16 && D(e.children, f, null, i, s, Ts(e, c), l, d), _ && qr(e, null, i, "created"), E(f, e, e.scopeId, l, i), m) {
			for (let e in m) e !== "value" && !ae(e) && a(f, e, null, m[e], c, i);
			"value" in m && a(f, "value", null, m.value, c), (p = m.onVnodeBeforeMount) && yc(p, i, e);
		}
		__VUE_PROD_DEVTOOLS__ && (fe(f, "__vnode", e, !0), fe(f, "__vueParentComponent", i, !0)), _ && qr(e, null, i, "beforeMount");
		let v = Ds(s, g);
		v && g.beforeEnter(f), r(f, t, n), ((p = m && m.onVnodeMounted) || v || _) && V(() => {
			try {
				p && yc(p, i, e), v && g.enter(f), _ && qr(e, null, i, "mounted");
			} finally {}
		}, s);
	}, E = (e, t, n, r, i) => {
		if (n && g(e, n), r) for (let t = 0; t < r.length; t++) g(e, r[t]);
		if (i) {
			let n = i.subTree;
			if (t === n || Ns(n.type) && (n.ssContent === t || n.ssFallback === t)) {
				let t = i.vnode;
				E(e, t, t.scopeId, t.slotScopeIds, i.parent);
			}
		}
	}, D = (e, t, n, r, i, a, o, s, c = 0) => {
		for (let l = c; l < e.length; l++) {
			let c = e[l] = s ? gc(e[l]) : hc(e[l]);
			v(null, c, t, n, r, i, a, o, s);
		}
	}, O = (e, t, n, r, i, o, s) => {
		let c = t.el = e.el;
		__VUE_PROD_DEVTOOLS__ && (c.__vnode = t);
		let { patchFlag: l, dynamicChildren: d, dirs: p } = t;
		l |= e.patchFlag & 16;
		let m = e.props || f, h = t.props || f, g;
		if (n && Es(n, !1), (g = h.onVnodeBeforeUpdate) && yc(g, n, t, e), p && qr(t, e, n, "beforeUpdate"), n && Es(n, !0), d && (!e.dynamicChildren || e.dynamicChildren.length !== d.length) && (l = 0, s = !1, d = null), (m.innerHTML && h.innerHTML == null || m.textContent && h.textContent == null) && u(c, ""), d ? k(e.dynamicChildren, d, c, n, r, Ts(t, i), o) : s || oe(e, t, c, null, n, r, Ts(t, i), o, !1), l > 0) {
			if (l & 16) A(c, m, h, n, i);
			else if (l & 2 && m.class !== h.class && a(c, "class", null, h.class, i), l & 4 && a(c, "style", m.style, h.style, i), l & 8) {
				let e = t.dynamicProps;
				for (let t = 0; t < e.length; t++) {
					let r = e[t], o = m[r], s = h[r];
					(s !== o || r === "value") && a(c, r, o, s, i, n);
				}
			}
			l & 1 && e.children !== t.children && u(c, t.children);
		} else !s && d == null && A(c, m, h, n, i);
		((g = h.onVnodeUpdated) || p) && V(() => {
			g && yc(g, n, t, e), p && qr(t, e, n, "updated");
		}, r);
	}, k = (e, t, n, r, i, a, o) => {
		for (let s = 0; s < t.length; s++) {
			let c = e[s], l = t[s], u = c.el && (c.type === H || !ic(c, l) || c.shapeFlag & 198) ? d(c.el) : n;
			v(c, l, u, null, r, i, a, o, !0);
		}
	}, A = (e, t, n, r, i) => {
		if (t !== n) {
			if (t !== f) for (let o in t) !ae(o) && !(o in n) && a(e, o, t[o], null, i, r);
			for (let o in n) {
				if (ae(o)) continue;
				let s = n[o], c = t[o];
				s !== c && o !== "value" && a(e, o, c, s, i, r);
			}
			"value" in n && a(e, "value", t.value, n.value, i);
		}
	}, ee = (e, t, n, i, a, o, c, l, u) => {
		let d = t.el = e ? e.el : s(""), f = t.anchor = e ? e.anchor : s(""), { patchFlag: p, dynamicChildren: m, slotScopeIds: h } = t;
		h && (l = l ? l.concat(h) : h), e == null ? (r(d, n, i), r(f, n, i), D(t.children || [], n, f, a, o, c, l, u)) : p > 0 && p & 64 && m && e.dynamicChildren && e.dynamicChildren.length === m.length ? (k(e.dynamicChildren, m, n, a, o, c, l), (t.key != null || a && t === a.subTree) && Os(e, t, !0)) : oe(e, t, n, f, a, o, c, l, u);
	}, te = (e, t, n, r, i, a, o, s, c) => {
		t.slotScopeIds = s, e == null ? t.shapeFlag & 512 ? i.ctx.activate(t, n, r, o, c) : ne(t, n, r, i, a, o, c) : re(e, t, c);
	}, ne = (e, t, n, r, i, a, o) => {
		let s = e.component = Sc(e, r, i);
		if (Ca(e) && (s.ctx.renderer = ve), jc(s, !1, o), s.asyncDep) {
			if (i && i.registerDep(s, j, o), !e.el) {
				let r = s.subTree = W(U);
				b(null, r, t, n), e.placeholder = r.el;
			}
		} else j(s, e, t, n, i, a, o);
	}, re = (e, t, n) => {
		let r = t.component = e.component;
		if ($o(e, t, n)) {
			if (r.asyncDep && !r.asyncResolved) {
				t.el = e.el, ie(r, t, n);
				return;
			}
			r.next = t, r.update();
		} else t.el = e.el, r.vnode = t;
	}, j = (e, t, n, r, i, a, o) => {
		let s = () => {
			if (e.isMounted) {
				let { next: t, bu: n, u: r, parent: s, vnode: c } = e;
				{
					let n = As(e);
					if (n) {
						t && (t.el = c.el, ie(e, t, o)), n.asyncDep.then(() => {
							V(() => {
								e.isUnmounted || l();
							}, i);
						});
						return;
					}
				}
				let u = t, f;
				Es(e, !1), t ? (t.el = c.el, ie(e, t, o)) : t = c, n && de(n), (f = t.props && t.props.onVnodeBeforeUpdate) && yc(f, s, t, c), Es(e, !0);
				let p = Yo(e), m = e.subTree;
				e.subTree = p, v(m, p, d(m.el), me(m), e, i, a), t.el = p.el, u === null && ns(e, p.el), r && V(r, i), (f = t.props && t.props.onVnodeUpdated) && V(() => yc(f, s, t, c), i), __VUE_PROD_DEVTOOLS__ && Fr(e);
			} else {
				let o, { el: s, props: c } = t, { bm: l, m: u, parent: d, root: f, type: p } = e, m = ba(t);
				if (Es(e, !1), l && de(l), !m && (o = c && c.onVnodeBeforeMount) && yc(o, d, t), Es(e, !0), s && be) {
					let t = () => {
						e.subTree = Yo(e), be(s, e.subTree, e, i, null);
					};
					m && p.__asyncHydrate ? p.__asyncHydrate(s, e, t) : t();
				} else {
					f.ce && f.ce._hasShadowRoot() && f.ce._injectChildStyle(p, e.parent ? e.parent.type : void 0);
					let o = e.subTree = Yo(e);
					v(null, o, n, r, e, i, a), t.el = o.el;
				}
				if (u && V(u, i), !m && (o = c && c.onVnodeMounted)) {
					let e = t;
					V(() => yc(o, d, e), i);
				}
				(t.shapeFlag & 256 || d && ba(d.vnode) && d.vnode.shapeFlag & 256) && e.a && V(e.a, i), e.isMounted = !0, __VUE_PROD_DEVTOOLS__ && Pr(e), t = n = r = null;
			}
		};
		e.scope.on();
		let c = e.effect = new Xe(s);
		e.scope.off();
		let l = e.update = c.run.bind(c), u = e.job = c.runIfDirty.bind(c);
		u.i = e, u.id = e.uid, c.scheduler = () => br(u), Es(e, !0), l();
	}, ie = (e, t, n) => {
		t.component = e;
		let r = e.vnode.props;
		e.vnode = t, e.next = null, ss(e, t.props, r, n), bs(e, t.children, n), pt(), Cr(e), mt();
	}, oe = (e, t, n, r, i, a, o, s, c = !1) => {
		let l = e && e.children, d = e ? e.shapeFlag : 0, f = t.children, { patchFlag: p, shapeFlag: m } = t;
		if (p > 0) {
			if (p & 128) {
				M(l, f, n, r, i, a, o, s, c);
				return;
			}
			if (p & 256) {
				se(l, f, n, r, i, a, o, s, c);
				return;
			}
		}
		m & 8 ? (d & 16 && pe(l, i, a), f !== l && u(n, f)) : d & 16 ? m & 16 ? M(l, f, n, r, i, a, o, s, c) : pe(l, i, a, !0) : (d & 8 && u(n, ""), m & 16 && D(f, n, r, i, a, o, s, c));
	}, se = (e, t, n, r, i, a, o, s, c) => {
		e = e || p, t = t || p;
		let l = e.length, u = t.length, d = Math.min(l, u), f = 0;
		for (; f < d; f++) {
			let r = t[f] = c ? gc(t[f]) : hc(t[f]);
			v(e[f], r, n, null, i, a, o, s, c);
		}
		l > u ? pe(e, i, a, !0, !1, d) : D(t, n, r, i, a, o, s, c, d);
	}, M = (e, t, n, r, i, a, o, s, c) => {
		let l = 0, u = t.length, d = e.length - 1, f = u - 1;
		for (; l <= d && l <= f;) {
			let r = e[l], u = t[l] = c ? gc(t[l]) : hc(t[l]);
			if (ic(r, u)) v(r, u, n, null, i, a, o, s, c);
			else break;
			l++;
		}
		for (; l <= d && l <= f;) {
			let r = e[d], l = t[f] = c ? gc(t[f]) : hc(t[f]);
			if (ic(r, l)) v(r, l, n, null, i, a, o, s, c);
			else break;
			d--, f--;
		}
		if (l > d) {
			if (l <= f) {
				let e = f + 1, d = e < u ? t[e].el : r;
				for (; l <= f;) v(null, t[l] = c ? gc(t[l]) : hc(t[l]), n, d, i, a, o, s, c), l++;
			}
		} else if (l > f) for (; l <= d;) N(e[l], i, a, !0), l++;
		else {
			let m = l, h = l, g = /* @__PURE__ */ new Map();
			for (l = h; l <= f; l++) {
				let e = t[l] = c ? gc(t[l]) : hc(t[l]);
				e.key != null && g.set(e.key, l);
			}
			let _, y = 0, b = f - h + 1, x = !1, S = 0, C = Array(b);
			for (l = 0; l < b; l++) C[l] = 0;
			for (l = m; l <= d; l++) {
				let r = e[l];
				if (y >= b) {
					N(r, i, a, !0);
					continue;
				}
				let u;
				if (r.key != null) u = g.get(r.key);
				else for (_ = h; _ <= f; _++) if (C[_ - h] === 0 && ic(r, t[_])) {
					u = _;
					break;
				}
				u === void 0 ? N(r, i, a, !0) : (C[u - h] = l + 1, u >= S ? S = u : x = !0, v(r, t[u], n, null, i, a, o, s, c), y++);
			}
			let w = x ? ks(C) : p;
			for (_ = w.length - 1, l = b - 1; l >= 0; l--) {
				let e = h + l, d = t[e], f = t[e + 1], p = e + 1 < u ? f.el || Ms(f) : r;
				C[l] === 0 ? v(null, d, n, p, i, a, o, s, c) : x && (_ < 0 || l !== w[_] ? ce(d, n, p, 2) : _--);
			}
		}
	}, ce = (e, t, n, a, o = null) => {
		let { el: s, type: c, transition: l, children: u, shapeFlag: d } = e;
		if (d & 6) ce(e.component.subTree, t, n, a);
		else if (d & 128) e.suspense.move(t, n, a);
		else if (d & 64) c.move(e, t, n, ve);
		else if (c === H) {
			r(s, t, n);
			for (let e = 0; e < u.length; e++) ce(u[e], t, n, a);
			r(e.anchor, t, n);
		} else if (c === qs) S(e, t, n);
		else if (a !== 2 && d & 1 && l) {
			if (a === 0) l.persisted && !s[bi] ? r(s, t, n) : (l.beforeEnter(s), r(s, t, n), V(() => l.enter(s), o));
			else {
				let { leave: a, delayLeave: o, afterLeave: c } = l, u = () => {
					e.ctx.isUnmounted ? i(s) : r(s, t, n);
				}, d = () => {
					let e = s._isLeaving || !!s[bi];
					s._isLeaving && s[bi](!0), l.persisted && !e ? u() : a(s, () => {
						u(), c && c();
					});
				};
				o ? o(s, u, d) : d();
			}
		} else r(s, t, n);
	}, N = (e, t, n, r = !1, i = !1) => {
		let { type: a, props: o, ref: s, children: c, dynamicChildren: l, shapeFlag: u, patchFlag: d, dirs: f, cacheIndex: p, memo: m } = e;
		if ((d === -2 || l && l.hasOnce) && (i = !1), s != null && (pt(), Vi(s, null, n, e, !0), mt()), p != null && (!e.ctx || e.ctx === t) && (t.renderCache[p] = void 0), u & 256) {
			t.ctx.deactivate(e);
			return;
		}
		let h = u & 1 && f, g = !ba(e), _;
		if (g && (_ = o && o.onVnodeBeforeUnmount) && yc(_, t, e), u & 6) P(e.component, n, r);
		else {
			if (u & 128) {
				e.suspense.unmount(n, r);
				return;
			}
			h && qr(e, null, t, "beforeUnmount"), u & 64 ? e.type.remove(e, t, n, ve, r) : l && !l.hasOnce && (a !== H || d > 0 && d & 64) ? pe(l, t, n, !1, !0) : (a === H && d & 384 || !i && u & 16) && pe(c, t, n), r && le(e);
		}
		let v = m != null && p == null;
		(g && (_ = o && o.onVnodeUnmounted) || h || v) && V(() => {
			_ && yc(_, t, e), h && qr(e, null, t, "unmounted"), v && (e.el = null);
		}, n);
	}, le = (e) => {
		let { type: t, el: n, anchor: r, transition: a } = e;
		if (t === H) {
			ue(n, r);
			return;
		}
		if (t === qs) {
			C(e), a && !a.persisted && a.afterLeave && a.afterLeave();
			return;
		}
		let o = () => {
			i(n), a && !a.persisted && a.afterLeave && a.afterLeave();
		};
		if (e.shapeFlag & 1 && a && !a.persisted) {
			let { leave: t, delayLeave: r } = a, i = () => t(n, o);
			r ? r(e.el, o, i) : i();
		} else o();
	}, ue = (e, t) => {
		let n;
		for (; e !== t;) n = h(e), i(e), e = n;
		i(t);
	}, P = (e, t, n) => {
		let { bum: r, scope: i, job: a, subTree: o, um: s, m: c, a: l } = e;
		js(c), js(l), r && de(r), i.stop(), a ? (a.flags |= 8, N(o, e, t, n)) : e.vnode.el && o && (o.transition = e.vnode.transition, N(o, e, t, n)), s && V(s, t), V(() => {
			e.isUnmounted = !0;
		}, t), __VUE_PROD_DEVTOOLS__ && Lr(e);
	}, pe = (e, t, n, r = !1, i = !1, a = 0) => {
		for (let o = a; o < e.length; o++) N(e[o], t, n, r, i);
	}, me = (e) => {
		if (e.shapeFlag & 6) return me(e.component.subTree);
		if (e.shapeFlag & 128) return e.suspense.next();
		let t = h(e.anchor || e.el), n = t && t[si];
		return n ? h(n) : t;
	}, he = !1, _e = (e, t, n) => {
		let r;
		e == null ? t._vnode && (N(t._vnode, null, null, !0), r = t._vnode.component) : v(t._vnode || null, e, t, null, null, null, n), t._vnode = e, he || (he = !0, Cr(r), wr(), he = !1);
	}, ve = {
		p: v,
		um: N,
		m: ce,
		r: le,
		mt: ne,
		mc: D,
		pc: oe,
		pbc: k,
		n: me,
		o: e
	}, ye, be;
	return t && ([ye, be] = t(ve)), {
		render: _e,
		hydrate: ye,
		createApp: Vo(_e, ye)
	};
}
function Ts({ type: e, props: t }, n) {
	return n === "svg" && e === "foreignObject" || n === "mathml" && e === "annotation-xml" && t && t.encoding && t.encoding.includes("html") ? void 0 : n;
}
function Es({ effect: e, job: t }, n) {
	n ? (e.flags |= 32, t.flags |= 4) : (e.flags &= -33, t.flags &= -5);
}
function Ds(e, t) {
	return (!e || e && !e.pendingBranch) && t && !t.persisted;
}
function Os(e, t, n = !1) {
	let r = e.children, i = t.children;
	if (S(r) && S(i)) for (let e = 0; e < r.length; e++) {
		let t = r[e], a = i[e];
		a.shapeFlag & 1 && !a.dynamicChildren && ((a.patchFlag <= 0 || a.patchFlag === 32) && (a = i[e] = gc(i[e]), a.el = t.el), !n && a.patchFlag !== -2 && Os(t, a)), a.type === Ks && (a.patchFlag === -1 && (a = i[e] = gc(a)), a.el = t.el), a.type === U && !a.el && (a.el = t.el);
	}
}
function ks(e) {
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
function As(e) {
	let t = e.subTree.component;
	if (t) return t.asyncDep && !t.asyncResolved ? t : As(t);
}
function js(e) {
	if (e) for (let t = 0; t < e.length; t++) e[t].flags |= 8;
}
function Ms(e) {
	if (e.placeholder) return e.placeholder;
	let t = e.component;
	return t ? Ms(t.subTree) : null;
}
var Ns = (e) => e.__isSuspense, Ps = 0, Fs = {
	name: "Suspense",
	__isSuspense: !0,
	process(e, t, n, r, i, a, o, s, c, l) {
		if (e == null) Ls(t, n, r, i, a, o, s, c, l);
		else {
			if (a && a.deps > 0 && !e.suspense.isInFallback && !a.isHydrating) {
				t.suspense = e.suspense, t.suspense.vnode = t, t.el = e.el;
				return;
			}
			Rs(e, t, n, r, i, o, s, c, l);
		}
	},
	hydrate: Bs,
	normalize: Vs
};
function Is(e, t) {
	let n = e.props && e.props[t];
	D(n) && n();
}
function Ls(e, t, n, r, i, a, o, s, c) {
	let { p: l, o: { createElement: u } } = c, d = u("div"), f = e.suspense = zs(e, i, r, t, d, n, a, o, s, c);
	l(null, f.pendingBranch = e.ssContent, d, null, r, f, a, o), f.deps > 0 ? (Is(e, "onPending"), Is(e, "onFallback"), l(null, e.ssFallback, t, n, r, null, a, o), Ws(f, e.ssFallback)) : f.resolve(!1, !0);
}
function Rs(e, t, n, r, i, a, o, s, { p: c, um: l, o: { createElement: u } }) {
	let d = t.suspense = e.suspense;
	d.vnode = t, t.el = e.el;
	let f = t.ssContent, p = t.ssFallback, { activeBranch: m, pendingBranch: h, isInFallback: g, isHydrating: _ } = d;
	if (h) d.pendingBranch = f, ic(h, f) ? (d.deps++, c(h, f, _ ? n : d.hiddenContainer, null, i, d, a, o, s), d.deps--, d.deps <= 0 ? d.resolve() : g && !_ && !d.isFallbackMountPending && (c(m, p, n, r, i, null, a, o, s), Ws(d, p))) : (d.pendingId = Ps++, _ ? (d.isHydrating = !1, d.activeBranch = h) : l(h, i, d), d.deps = 0, d.effects.length = 0, d.hiddenContainer = u("div"), g ? (c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0 ? d.resolve() : d.isFallbackMountPending || (c(m, p, n, r, i, null, a, o, s), Ws(d, p))) : m && ic(m, f) ? (c(m, f, n, r, i, d, a, o, s), d.resolve(!0)) : (c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0 && d.resolve()));
	else if (m && ic(m, f)) c(m, f, n, r, i, d, a, o, s), Ws(d, f);
	else if (Is(t, "onPending"), d.pendingBranch = f, d.pendingId = f.shapeFlag & 512 ? f.component.suspenseId : Ps++, c(null, f, d.hiddenContainer, null, i, d, a, o, s), d.deps <= 0) d.resolve();
	else {
		let { timeout: e, pendingId: t } = d;
		e > 0 ? setTimeout(() => {
			d.pendingId === t && d.fallback(p);
		}, e) : e === 0 && d.fallback(p);
	}
}
function zs(e, t, n, r, i, a, o, s, c, l, u = !1) {
	let { p: d, m: f, um: p, n: m, o: { parentNode: h, remove: g } } = l, _, v = Gs(e);
	v && t && t.pendingBranch && (_ = t.pendingId, t.deps++);
	let y = e.props ? me(e.props.timeout) : void 0, b = a, x = {
		vnode: e,
		parent: t,
		parentComponent: n,
		namespace: o,
		container: r,
		hiddenContainer: i,
		deps: 0,
		pendingId: Ps++,
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
					s === x.pendingId && (f(o, u, a === b && !e ? m(i) : a, 0), Sr(c), d && r.ssFallback && (r.ssFallback.el = null));
				}), i && !x.isFallbackMountPending && (h(i.el) === u && (a = m(i), e = !0), p(i, l, x, !0), !g && d && r.ssFallback && V(() => r.ssFallback.el = null, x)), g || f(o, u, a, 0);
			}
			x.isFallbackMountPending = !1, Ws(x, o), x.pendingBranch = null, x.isInFallback = !1;
			let y = x.parent, S = !1;
			for (; y;) {
				if (y.pendingBranch) {
					for (let e = 0; e < c.length; e++) y.effects.push(c[e]);
					S = !0;
					break;
				}
				y = y.parent;
			}
			!S && !g && Sr(c), x.effects = [], v && t && t.pendingBranch && _ === t.pendingId && (_ = void 0, t.deps--, t.deps === 0 && !n && t.resolve()), Is(r, "onResolve");
		},
		fallback(e) {
			if (!x.pendingBranch) return;
			let { vnode: t, activeBranch: n, parentComponent: r, container: i, namespace: a } = x;
			Is(t, "onFallback");
			let o = m(n), l = () => {
				if (x.isFallbackMountPending = !1, !x.isInFallback) return;
				let e = x.vnode.ssFallback;
				d(null, e, i, o, r, null, a, s, c), Ws(x, e);
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
				lr(t, e, 0);
			}).then((a) => {
				if (e.isUnmounted || x.isUnmounted || x.pendingId !== e.suspenseId) return;
				if (Oc(), i && !e.scope.active) {
					r && --x.deps === 0 && x.resolve();
					return;
				}
				e.asyncResolved = !0;
				let { vnode: s } = e;
				Nc(e, a, !1), i && (s.el = i);
				let c = !i && e.subTree.el;
				t(e, s, h(i || e.subTree.el), i ? null : m(e.subTree), x, o, n), c && (s.placeholder = null, g(c)), ns(e, s.el), r && --x.deps === 0 && x.resolve();
			});
		},
		unmount(e, t) {
			x.isUnmounted = !0, x.activeBranch && p(x.activeBranch, n, e, t), x.pendingBranch && p(x.pendingBranch, n, e, t);
		}
	};
	return x;
}
function Bs(e, t, n, r, i, a, o, s, c) {
	let l = t.suspense = zs(t, r, n, e.parentNode, document.createElement("div"), null, i, a, o, s, !0), u = c(e, l.pendingBranch = t.ssContent, n, l, a, o);
	return l.deps === 0 && l.resolve(!1, !0), u;
}
function Vs(e) {
	let { shapeFlag: t, children: n } = e, r = t & 32;
	e.ssContent = Hs(r ? n.default : n), e.ssFallback = r ? Hs(n.fallback) : W(U);
}
function Hs(e) {
	let t;
	if (D(e)) {
		let n = Qs && e._c;
		n && (e._d = !1, Xs()), e = e(), n && (e._d = !0, t = Ys, Zs());
	}
	return S(e) && (e = Xo(e)), e = hc(e), t && !e.dynamicChildren && (e.dynamicChildren = t.filter((t) => t !== e)), e;
}
function Us(e, t) {
	t && t.pendingBranch ? S(e) ? t.effects.push(...e) : t.effects.push(e) : Sr(e);
}
function Ws(e, t) {
	e.activeBranch = t;
	let { vnode: n, parentComponent: r } = e, i = t.el;
	for (; !i && t.component;) t = t.component.subTree, i = t.el;
	n.el = i, r && r.subTree === n && (r.vnode.el = i, ns(r, i));
}
function Gs(e) {
	let t = e.props && e.props.suspensible;
	return t != null && t !== !1;
}
var H = /* @__PURE__ */ Symbol.for("v-fgt"), Ks = /* @__PURE__ */ Symbol.for("v-txt"), U = /* @__PURE__ */ Symbol.for("v-cmt"), qs = /* @__PURE__ */ Symbol.for("v-stc"), Js = [], Ys = null;
function Xs(e = !1) {
	Js.push(Ys = e ? null : []);
}
function Zs() {
	Js.pop(), Ys = Js[Js.length - 1] || null;
}
var Qs = 1;
function $s(e, t = !1) {
	Qs += e, e < 0 && Ys && t && (Ys.hasOnce = !0);
}
function ec(e) {
	return e.dynamicChildren = Qs > 0 ? Ys || p : null, Zs(), Qs > 0 && Ys && Ys.push(e), e;
}
function tc(e, t, n, r, i, a) {
	return ec(cc(e, t, n, r, i, a, !0));
}
function nc(e, t, n, r, i) {
	return ec(W(e, t, n, r, i, !0));
}
function rc(e) {
	return e ? e.__v_isVNode === !0 : !1;
}
function ic(e, t) {
	return e.type === t.type && e.key === t.key;
}
function ac(e) {}
var oc = ({ key: e }) => e ?? null, sc = ({ ref: e, ref_key: t, ref_for: n }) => (typeof e == "number" && (e = "" + e), e == null ? null : O(e) || /* @__PURE__ */ z(e) || D(e) ? {
	i: B,
	r: e,
	k: t,
	f: !!n
} : e);
function cc(e, t = null, n = null, r = 0, i = null, a = e === H ? 0 : 1, o = !1, s = !1) {
	let c = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e,
		props: t,
		key: t && oc(t),
		ref: t && sc(t),
		scopeId: Br,
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
		ctx: B
	};
	return s ? (_c(c, n), a & 128 && e.normalize(c)) : n && (c.shapeFlag |= O(n) ? 8 : 16), Qs > 0 && !o && Ys && (c.patchFlag > 0 || a & 6) && c.patchFlag !== 32 && Ys.push(c), c;
}
var W = lc;
function lc(e, t = null, n = null, r = 0, i = null, a = !1) {
	if ((!e || e === qa) && (e = U), rc(e)) {
		let r = dc(e, t, !0);
		return n && _c(r, n), Qs > 0 && !a && Ys && (r.shapeFlag & 6 ? Ys[Ys.indexOf(e)] = r : Ys.push(r)), r.patchFlag = -2, r;
	}
	if (Kc(e) && (e = e.__vccOpts), t) {
		t = uc(t);
		let { class: e, style: n } = t;
		e && !O(e) && (t.class = Ce(e)), A(n) && (/* @__PURE__ */ _n(n) && !S(n) && (n = v({}, n)), t.style = ve(n));
	}
	let o = O(e) ? 1 : Ns(e) ? 128 : ci(e) ? 64 : A(e) ? 4 : D(e) ? 2 : 0;
	return cc(e, t, n, r, i, o, a, !0);
}
function uc(e) {
	return e ? /* @__PURE__ */ _n(e) || as(e) ? v({}, e) : e : null;
}
function dc(e, t, n = !1, r = !1) {
	let { props: i, ref: a, patchFlag: o, children: s, transition: c } = e, l = t ? vc(i || {}, t) : i, u = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e.type,
		props: l,
		key: l && oc(l),
		ref: t && t.ref ? n && a ? S(a) ? a.concat(sc(t)) : [a, sc(t)] : sc(t) : a,
		scopeId: e.scopeId,
		slotScopeIds: e.slotScopeIds,
		children: s,
		target: e.target,
		targetStart: e.targetStart,
		targetAnchor: e.targetAnchor,
		staticCount: e.staticCount,
		shapeFlag: e.shapeFlag,
		patchFlag: t && e.type !== H ? o === -1 ? 16 : o | 16 : o,
		dynamicProps: e.dynamicProps,
		dynamicChildren: e.dynamicChildren,
		appContext: e.appContext,
		dirs: e.dirs,
		transition: c,
		component: e.component,
		suspense: e.suspense,
		ssContent: e.ssContent && dc(e.ssContent),
		ssFallback: e.ssFallback && dc(e.ssFallback),
		placeholder: e.placeholder,
		el: e.el,
		anchor: e.anchor,
		ctx: e.ctx,
		ce: e.ce,
		cacheIndex: e.cacheIndex
	};
	return c && r && Ni(u, c.clone(u)), u;
}
function fc(e = " ", t = 0) {
	return W(Ks, null, e, t);
}
function pc(e, t) {
	let n = W(qs, null, e);
	return n.staticCount = t, n;
}
function mc(e = "", t = !1) {
	return t ? (Xs(), nc(U, null, e)) : W(U, null, e);
}
function hc(e) {
	return e == null || typeof e == "boolean" ? W(U) : S(e) ? W(H, null, e.slice()) : rc(e) ? gc(e) : W(Ks, null, String(e));
}
function gc(e) {
	return e.el === null && e.patchFlag !== -1 || e.memo ? e : dc(e);
}
function _c(e, t) {
	let n = 0, { shapeFlag: r } = e;
	if (t == null) t = null;
	else if (S(t)) n = 16;
	else if (typeof t == "object") {
		if (r & 65) {
			let n = t.default;
			n && (n._c && (n._d = !1), _c(e, n()), n._c && (n._d = !0));
			return;
		}
		{
			n = 32;
			let r = t._;
			!r && !as(t) ? t._ctx = B : r === 3 && B && (B.slots._ === 1 ? t._ = 1 : (t._ = 2, e.patchFlag |= 1024));
		}
	} else if (D(t)) {
		if (r & 65) {
			_c(e, { default: t });
			return;
		}
		t = {
			default: t,
			_ctx: B
		}, n = 32;
	} else t = String(t), r & 64 ? (n = 16, t = [fc(t)]) : n = 8;
	e.children = t, e.shapeFlag |= n;
}
function vc(...e) {
	let t = {};
	for (let n = 0; n < e.length; n++) {
		let r = e[n];
		for (let e in r) if (e === "class") t.class !== r.class && (t.class = Ce([t.class, r.class]));
		else if (e === "style") t.style = ve([t.style, r.style]);
		else if (g(e)) {
			let n = t[e], i = r[e];
			i && n !== i && !(S(n) && n.includes(i)) ? t[e] = n ? [].concat(n, i) : i : i == null && n == null && !_(e) && (t[e] = i);
		} else e !== "" && (t[e] = r[e]);
	}
	return t;
}
function yc(e, t, n, r = null) {
	cr(e, t, 7, [n, r]);
}
var bc = zo(), xc = 0;
function Sc(e, t, n) {
	let r = e.type, i = (t ? t.appContext : e.appContext) || bc, a = {
		uid: xc++,
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
		scope: new Ge(!0),
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
		propsOptions: ds(r, i),
		emitsOptions: qo(r, i),
		emit: null,
		emitted: null,
		propsDefaults: f,
		inheritAttrs: r.inheritAttrs,
		ctx: f,
		data: f,
		props: f,
		attrs: f,
		slots: f,
		refs: f,
		setupState: f,
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
	return a.ctx = { _: a }, a.root = t ? t.root : a, a.emit = Go.bind(null, a), e.ce && e.ce(a), a;
}
var Cc = null, wc = () => Cc || B, Tc, Ec;
{
	let e = ge(), t = (t, n) => {
		let r;
		return (r = e[t]) || (r = e[t] = []), r.push(n), (e) => {
			r.length > 1 ? r.forEach((t) => t(e)) : r[0](e);
		};
	};
	Tc = t("__VUE_INSTANCE_SETTERS__", (e) => Cc = e), Ec = t("__VUE_SSR_SETTERS__", (e) => Ac = e);
}
var Dc = (e) => {
	let t = Cc;
	return Tc(e), e.scope.on(), () => {
		e.scope.off(), Tc(t);
	};
}, Oc = () => {
	Cc && Cc.scope.off(), Tc(null);
};
function kc(e) {
	return e.vnode.shapeFlag & 4;
}
var Ac = !1;
function jc(e, t = !1, n = !1) {
	t && Ec(t);
	let { props: r, children: i } = e.vnode, a = kc(e);
	os(e, r, a, t), ys(e, i, n || t);
	let o = a ? Mc(e, t) : void 0;
	return t && Ec(!1), o;
}
function Mc(e, t) {
	let n = e.type;
	e.accessCache = /* @__PURE__ */ Object.create(null), e.proxy = new Proxy(e.ctx, oo);
	let { setup: r } = n;
	if (r) {
		pt();
		let n = e.setupContext = r.length > 1 ? Bc(e) : null, i = Dc(e), a = sr(r, e, 0, [e.props, n]), o = ee(a);
		if (mt(), i(), (o || e.sp) && !ba(e) && Li(e), o) {
			if (a.then(Oc, Oc), t) return a.then((n) => {
				Ec(!0);
				try {
					Nc(e, n, t);
				} finally {
					Ec(!1);
				}
			}).catch((t) => {
				lr(t, e, 0);
			});
			e.asyncDep = a;
		} else Nc(e, a, t);
	} else Rc(e, t);
}
function Nc(e, t, n) {
	D(t) ? e.type.__ssrInlineRender ? e.ssrRender = t : e.render = t : A(t) && (__VUE_PROD_DEVTOOLS__ && (e.devtoolsRawSetupState = t), e.setupState = kn(t)), Rc(e, n);
}
var Pc, Fc;
function Ic(e) {
	Pc = e, Fc = (e) => {
		e.render._rc && (e.withProxy = new Proxy(e.ctx, so));
	};
}
var Lc = () => !Pc;
function Rc(e, t, n) {
	let r = e.type;
	if (!e.render) {
		if (!t && Pc && !r.render) {
			let t = r.template || __VUE_OPTIONS_API__ && ko(e).template;
			if (t) {
				let { isCustomElement: n, compilerOptions: i } = e.appContext.config, { delimiters: a, compilerOptions: o } = r, s = v(v({
					isCustomElement: n,
					delimiters: a
				}, i), o);
				r.render = Pc(t, s);
			}
		}
		e.render = r.render || m, Fc && Fc(e);
	}
	if (__VUE_OPTIONS_API__) {
		let t = Dc(e);
		pt();
		try {
			To(e);
		} finally {
			mt(), t();
		}
	}
}
var zc = { get(e, t) {
	return wt(e, "get", ""), e[t];
} };
function Bc(e) {
	return {
		attrs: new Proxy(e.attrs, zc),
		slots: e.slots,
		emit: e.emit,
		expose: (t) => {
			e.exposed = t || {};
		}
	};
}
function Vc(e) {
	return e.exposed ? e.exposeProxy || (e.exposeProxy = new Proxy(kn(vn(e.exposed)), {
		get(t, n) {
			if (n in t) return t[n];
			if (n in io) return io[n](e);
		},
		has(e, t) {
			return t in e || t in io;
		}
	})) : e.proxy;
}
var Hc = /(?:^|[-_])\w/g, Uc = (e) => e.replace(Hc, (e) => e.toUpperCase()).replace(/[-_]/g, "");
function Wc(e, t = !0) {
	return D(e) ? e.displayName || e.name : e.name || t && e.__name;
}
function Gc(e, t, n = !1) {
	let r = Wc(t);
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
	return r ? Uc(r) : n ? "App" : "Anonymous";
}
function Kc(e) {
	return D(e) && "__vccOpts" in e;
}
var qc = (e, t) => /* @__PURE__ */ Rn(e, t, Ac);
function Jc(e, t, n) {
	try {
		$s(-1);
		let r = arguments.length;
		return r === 2 ? A(t) && !S(t) ? rc(t) ? W(e, null, [t]) : W(e, t) : W(e, null, t) : (r > 3 ? n = Array.prototype.slice.call(arguments, 2) : r === 3 && rc(n) && (n = [n]), W(e, t, n));
	} finally {
		$s(1);
	}
}
function Yc() {}
function Xc(e, t, n, r) {
	let i = n[r];
	if (i && Zc(i, e)) return i;
	let a = t();
	return a.memo = e.slice(), a.cacheIndex = r, n[r] = a;
}
function Zc(e, t) {
	let n = e.memo;
	if (n.length != t.length) return !1;
	for (let e = 0; e < n.length; e++) if (P(n[e], t[e])) return !1;
	return Qs > 0 && Ys && Ys.push(e), !0;
}
var Qc = "3.5.43", $c = m, el = or, tl = Dr, nl = jr, rl = {
	createComponentInstance: Sc,
	setupComponent: jc,
	renderComponentRoot: Yo,
	setCurrentRenderingInstance: Vr,
	isVNode: rc,
	normalizeVNode: hc,
	getComponentPublicInstance: Vc,
	ensureValidVNode: to,
	pushWarningContext: Yn,
	popWarningContext: Xn
}, il = void 0, al = typeof window < "u" && window.trustedTypes;
if (al) try {
	il = /* @__PURE__ */ al.createPolicy("vue", { createHTML: (e) => e });
} catch {}
var ol = il ? (e) => il.createHTML(e) : (e) => e, sl = "http://www.w3.org/2000/svg", cl = "http://www.w3.org/1998/Math/MathML", ll = typeof document < "u" ? document : null, ul = ll && /* @__PURE__ */ ll.createElement("template"), dl = {
	insert: (e, t, n) => {
		t.insertBefore(e, n || null);
	},
	remove: (e) => {
		let t = e.parentNode;
		t && t.removeChild(e);
	},
	createElement: (e, t, n, r) => {
		let i = t === "svg" ? ll.createElementNS(sl, e) : t === "mathml" ? ll.createElementNS(cl, e) : n ? ll.createElement(e, { is: n }) : ll.createElement(e);
		return e === "select" && r && r.multiple != null && i.setAttribute("multiple", r.multiple), i;
	},
	createText: (e) => ll.createTextNode(e),
	createComment: (e) => ll.createComment(e),
	setText: (e, t) => {
		e.nodeValue = t;
	},
	setElementText: (e, t) => {
		e.textContent = t;
	},
	parentNode: (e) => e.parentNode,
	nextSibling: (e) => e.nextSibling,
	querySelector: (e) => ll.querySelector(e),
	setScopeId(e, t) {
		e.setAttribute(t, "");
	},
	insertStaticContent(e, t, n, r, i, a) {
		let o = n ? n.previousSibling : t.lastChild;
		if (i && (i === a || i.nextSibling)) for (; t.insertBefore(i.cloneNode(!0), n), i !== a && (i = i.nextSibling););
		else {
			ul.innerHTML = ol(r === "svg" ? `<svg>${e}</svg>` : r === "mathml" ? `<math>${e}</math>` : e);
			let i = ul.content;
			if (r === "svg" || r === "mathml") {
				let e = i.firstChild;
				for (; e.firstChild;) i.appendChild(e.firstChild);
				i.removeChild(e);
			}
			t.insertBefore(i, n);
		}
		return [o ? o.nextSibling : t.firstChild, n ? n.previousSibling : t.lastChild];
	}
}, fl = "transition", pl = "animation", ml = /* @__PURE__ */ Symbol("_vtc"), hl = {
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
}, gl = /* @__PURE__ */ v({}, wi, hl), _l = /* @__PURE__ */ ((e) => (e.displayName = "Transition", e.props = gl, e))((e, { slots: t }) => Jc(Oi, bl(e), t)), vl = (e, t = []) => {
	S(e) ? e.forEach((e) => e(...t)) : e && e(...t);
}, yl = (e) => e ? S(e) ? e.some((e) => e.length > 1) : e.length > 1 : !1;
function bl(e) {
	let t = {};
	for (let n in e) n in hl || (t[n] = e[n]);
	if (e.css === !1) return t;
	let { name: n = "v", type: r, duration: i, enterFromClass: a = `${n}-enter-from`, enterActiveClass: o = `${n}-enter-active`, enterToClass: s = `${n}-enter-to`, appearFromClass: c = a, appearActiveClass: l = o, appearToClass: u = s, leaveFromClass: d = `${n}-leave-from`, leaveActiveClass: f = `${n}-leave-active`, leaveToClass: p = `${n}-leave-to` } = e, m = xl(i), h = m && m[0], g = m && m[1], { onBeforeEnter: _, onEnter: y, onEnterCancelled: b, onLeave: x, onLeaveCancelled: S, onBeforeAppear: C = _, onAppear: w = y, onAppearCancelled: T = b } = t, E = (e, t, n, r) => {
		e._enterCancelled = r, wl(e, t ? u : s), wl(e, t ? l : o), n && n();
	}, D = (e, t) => {
		e._isLeaving = !1, wl(e, d), wl(e, p), wl(e, f), t && t();
	}, O = (e) => (t, n) => {
		let i = e ? w : y, o = () => E(t, e, n);
		vl(i, [t, o]), Tl(() => {
			wl(t, e ? c : a), Cl(t, e ? u : s), yl(i) || Dl(t, r, h, o);
		});
	};
	return v(t, {
		onBeforeEnter(e) {
			vl(_, [e]), Cl(e, a), Cl(e, o);
		},
		onBeforeAppear(e) {
			vl(C, [e]), Cl(e, c), Cl(e, l);
		},
		onEnter: O(!1),
		onAppear: O(!0),
		onLeave(e, t) {
			e._isLeaving = !0;
			let n = () => D(e, t);
			Cl(e, d), e._enterCancelled ? (Cl(e, f), jl(e)) : (jl(e), Cl(e, f)), Tl(() => {
				e._isLeaving && (wl(e, d), Cl(e, p), yl(x) || Dl(e, r, g, n));
			}), vl(x, [e, n]);
		},
		onEnterCancelled(e) {
			E(e, !1, void 0, !0), vl(b, [e]);
		},
		onAppearCancelled(e) {
			E(e, !0, void 0, !0), vl(T, [e]);
		},
		onLeaveCancelled(e) {
			D(e), vl(S, [e]);
		}
	});
}
function xl(e) {
	if (e == null) return null;
	if (A(e)) return [Sl(e.enter), Sl(e.leave)];
	{
		let t = Sl(e);
		return [t, t];
	}
}
function Sl(e) {
	return me(e);
}
function Cl(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.add(t)), (e[ml] || (e[ml] = /* @__PURE__ */ new Set())).add(t);
}
function wl(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.remove(t));
	let n = e[ml];
	n && (n.delete(t), n.size || (e[ml] = void 0));
}
function Tl(e) {
	requestAnimationFrame(() => {
		requestAnimationFrame(e);
	});
}
var El = 0;
function Dl(e, t, n, r) {
	let i = e._endId = ++El, a = () => {
		i === e._endId && r();
	};
	if (n != null) return setTimeout(a, n);
	let { type: o, timeout: s, propCount: c } = Ol(e, t);
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
function Ol(e, t) {
	let n = window.getComputedStyle(e), r = (e) => (n[e] || "").split(", "), i = r(`${fl}Delay`), a = r(`${fl}Duration`), o = kl(i, a), s = r(`${pl}Delay`), c = r(`${pl}Duration`), l = kl(s, c), u = null, d = 0, f = 0;
	t === fl ? o > 0 && (u = fl, d = o, f = a.length) : t === pl ? l > 0 && (u = pl, d = l, f = c.length) : (d = Math.max(o, l), u = d > 0 ? o > l ? fl : pl : null, f = u ? u === fl ? a.length : c.length : 0);
	let p = u === fl && /\b(?:transform|all)(?:,|$)/.test(r(`${fl}Property`).toString());
	return {
		type: u,
		timeout: d,
		propCount: f,
		hasTransform: p
	};
}
function kl(e, t) {
	for (; e.length < t.length;) e = e.concat(e);
	return Math.max(...t.map((t, n) => Al(t) + Al(e[n])));
}
function Al(e) {
	return e === "auto" ? 0 : Number(e.slice(0, -1).replace(",", ".")) * 1e3;
}
function jl(e) {
	return (e ? e.ownerDocument : document).body.offsetHeight;
}
function Ml(e, t, n) {
	let r = e[ml];
	r && (t = (t ? [t, ...r] : [...r]).join(" ")), t == null ? e.removeAttribute("class") : n ? e.setAttribute("class", t) : e.className = t;
}
var Nl = /* @__PURE__ */ Symbol("_vod"), Pl = /* @__PURE__ */ Symbol("_vsh"), Fl = {
	name: "show",
	beforeMount(e, { value: t }, { transition: n }) {
		e[Nl] = e.style.display === "none" ? "" : e.style.display, n && t ? n.beforeEnter(e) : Il(e, t);
	},
	mounted(e, { value: t }, { transition: n }) {
		n && t && n.enter(e);
	},
	updated(e, { value: t, oldValue: n }, { transition: r }) {
		!t != !n && (r ? t ? (r.beforeEnter(e), Il(e, !0), r.enter(e)) : r.leave(e, () => {
			Il(e, !1);
		}) : Il(e, t));
	},
	beforeUnmount(e, { value: t }) {
		Il(e, t);
	}
};
function Il(e, t) {
	e.style.display = t ? e[Nl] : "none", e[Pl] = !t;
}
function Ll() {
	Fl.getSSRProps = ({ value: e }) => {
		if (!e) return { style: { display: "none" } };
	};
}
var Rl = /* @__PURE__ */ Symbol("");
function zl(e) {
	let t = wc();
	if (!t) return;
	let n = t.ut = (n = e(t.proxy)) => {
		Array.from(document.querySelectorAll(`[data-v-owner="${t.uid}"]`)).forEach((e) => Vl(e, n));
	}, r = () => {
		let r = e(t.proxy);
		t.ce ? Vl(t.ce, r) : Bl(t.subTree, r), n(r);
	};
	Ia(() => {
		Sr(r);
	}), Fa(() => {
		ni(r, m, { flush: "post" });
		let e = new MutationObserver(r);
		e.observe(t.subTree.el.parentNode, { childList: !0 }), za(() => e.disconnect());
	});
}
function Bl(e, t) {
	if (e.shapeFlag & 128) {
		let n = e.suspense;
		e = n.activeBranch, n.pendingBranch && !n.isHydrating && n.effects.push(() => {
			Bl(n.activeBranch, t);
		});
	}
	for (; e.component;) e = e.component.subTree;
	if (e.shapeFlag & 1 && e.el) Vl(e.el, t);
	else if (e.type === H) e.children.forEach((e) => Bl(e, t));
	else if (e.type === qs) {
		let { el: n, anchor: r } = e;
		for (; n && (Vl(n, t), n !== r);) n = n.nextSibling;
	}
}
function Vl(e, t) {
	if (e.nodeType === 1) {
		let n = e.style, r = "";
		for (let e in t) {
			let i = We(t[e]);
			n.setProperty(`--${e}`, i), r += `--${e}: ${i};`;
		}
		n[Rl] = r;
	}
}
var Hl = /(?:^|;)\s*display\s*:/;
function Ul(e, t, n) {
	let r = e.style, i = O(n), a = !1;
	if (n && !i) {
		if (t) {
			if (O(t)) for (let e of t.split(";")) {
				let t = e.slice(0, e.indexOf(":")).trim();
				n[t] ?? Gl(r, t, "");
			}
			else for (let e in t) n[e] ?? Gl(r, e, "");
		}
		for (let i in n) {
			i === "display" && (a = !0);
			let o = n[i];
			o == null ? Gl(r, i, "") : Yl(e, i, !O(t) && t ? t[i] : void 0, o) || Gl(r, i, o);
		}
	} else if (i) {
		if (t !== n) {
			let e = r[Rl];
			e && (n += ";" + e), r.cssText = n, a = Hl.test(n);
		}
	} else t && e.removeAttribute("style");
	Nl in e && (e[Nl] = a ? r.display : "", e[Pl] && (r.display = "none"));
}
var Wl = /\s*!important$/;
function Gl(e, t, n) {
	if (S(n)) n.forEach((n) => Gl(e, t, n));
	else if (n ?? (n = ""), t.startsWith("--")) Wl.test(n) ? e.setProperty(t, n.replace(Wl, ""), "important") : e.setProperty(t, n);
	else {
		let r = Jl(e, t);
		Wl.test(n) ? e.setProperty(N(r), n.replace(Wl, ""), "important") : e[r] = n;
	}
}
var Kl = [
	"Webkit",
	"Moz",
	"ms"
], ql = {};
function Jl(e, t) {
	let n = ql[t];
	if (n) return n;
	let r = M(t);
	if (r !== "filter" && r in e) return ql[t] = r;
	r = le(r);
	for (let n = 0; n < Kl.length; n++) {
		let i = Kl[n] + r;
		if (i in e) return ql[t] = i;
	}
	return t;
}
function Yl(e, t, n, r) {
	return e.tagName === "TEXTAREA" && (t === "width" || t === "height") && O(r) && n === r;
}
var Xl = "http://www.w3.org/1999/xlink";
function Zl(e, t, n, r, i, a = Ee(t)) {
	r && t.startsWith("xlink:") ? n == null ? e.removeAttributeNS(Xl, t.slice(6, t.length)) : e.setAttributeNS(Xl, t, n) : n == null || a && !Oe(n) ? e.removeAttribute(t) : e.setAttribute(t, a ? "" : k(n) ? String(n) : n);
}
function Ql(e, t, n, r, i) {
	if (t === "innerHTML" || t === "textContent") {
		n != null && (e[t] = t === "innerHTML" ? ol(n) : n);
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
		r === "boolean" ? n = Oe(n) : n == null && r === "string" ? (n = "", o = !0) : r === "number" && (n = 0, o = !0);
	}
	try {
		e[t] = n;
	} catch {}
	o && e.removeAttribute(i || t);
}
function $l(e, t, n, r) {
	e.addEventListener(t, n, r);
}
function eu(e, t, n, r) {
	e.removeEventListener(t, n, r);
}
var tu = /* @__PURE__ */ Symbol("_vei");
function nu(e, t, n, r, i = null) {
	let a = e[tu] || (e[tu] = {}), o = a[t];
	if (r && o) o.value = r;
	else {
		let [n, s] = au(t);
		r ? $l(e, n, a[t] = lu(r, i), s) : o && (eu(e, n, o, s), a[t] = void 0);
	}
}
var ru = /(Once|Passive|Capture)$/, iu = /^on:?(?:Once|Passive|Capture)$/;
function au(e) {
	let t, n;
	for (; (n = e.match(ru)) && !iu.test(e);) t || (t = {}), e = e.slice(0, e.length - n[1].length), t[n[1].toLowerCase()] = !0;
	return [e[2] === ":" ? e.slice(3) : N(e.slice(2)), t];
}
var ou = 0, su = /* @__PURE__ */ Promise.resolve(), cu = () => ou || (su.then(() => ou = 0), ou = Date.now());
function lu(e, t) {
	let n = (e) => {
		if (!e._vts) e._vts = Date.now();
		else if (e._vts <= n.attached) return;
		let r = n.value;
		if (S(r)) {
			let n = e.stopImmediatePropagation;
			e.stopImmediatePropagation = () => {
				n.call(e), e._stopped = !0;
			};
			let i = r.slice(), a = [e];
			for (let n = 0; n < i.length && !e._stopped; n++) {
				let e = i[n];
				e && cr(e, t, 5, a);
			}
		} else cr(r, t, 5, [e]);
	};
	return n.value = e, n.attached = cu(), n;
}
var uu = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && e.charCodeAt(2) > 96 && e.charCodeAt(2) < 123, du = (e, t, n, r, i, a) => {
	let o = i === "svg";
	t === "class" ? Ml(e, r, o) : t === "style" ? Ul(e, n, r) : g(t) ? _(t) || nu(e, t, n, r, a) : (t[0] === "." ? (t = t.slice(1), 1) : t[0] === "^" ? (t = t.slice(1), 0) : fu(e, t, r, o)) ? (Ql(e, t, r), !e.tagName.includes("-") && (t === "value" || t === "checked" || t === "selected") && Zl(e, t, r, o, a, t !== "value")) : e._isVueCE && (pu(e, t) || e._def.__asyncLoader && (/[A-Z]/.test(t) || !O(r))) ? Ql(e, M(t), r, a, t) : (t === "true-value" ? e._trueValue = r : t === "false-value" && (e._falseValue = r), Zl(e, t, r, o));
};
function fu(e, t, n, r) {
	if (r) return !!(t === "innerHTML" || t === "textContent" || t in e && uu(t) && D(n));
	if (t === "spellcheck" || t === "draggable" || t === "translate" || t === "autocorrect" || t === "sandbox" && e.tagName === "IFRAME" || t === "form" || t === "list" && e.tagName === "INPUT" || t === "type" && e.tagName === "TEXTAREA") return !1;
	if (t === "width" || t === "height") {
		let t = e.tagName;
		if (t === "IMG" || t === "VIDEO" || t === "CANVAS" || t === "SOURCE") return !1;
	}
	return uu(t) && O(n) ? !1 : t in e;
}
function pu(e, t) {
	let n = e._def.props;
	if (!n) return !1;
	let r = M(t);
	return Array.isArray(n) ? n.some((e) => M(e) === r) : Object.keys(n).some((e) => M(e) === r);
}
var mu = {};
// @__NO_SIDE_EFFECTS__
function hu(e, t, n) {
	let r = /* @__PURE__ */ Fi(e, t);
	j(r) && (r = v({}, r, t));
	class i extends vu {
		constructor(e) {
			super(r, e, n);
		}
	}
	return i.def = r, i;
}
var gu = /* @__NO_SIDE_EFFECTS__ */ ((e, t) => /* @__PURE__ */ hu(e, t, ud)), _u = typeof HTMLElement < "u" ? HTMLElement : class {}, vu = class e extends _u {
	constructor(e, t = {}, n = ld) {
		super(), this._def = e, this._props = t, this._createApp = n, this._isVueCE = !0, this._instance = null, this._app = null, this._nonce = this._def.nonce, this._connected = !1, this._resolved = !1, this._patching = !1, this._dirty = !1, this._numberProps = null, this._styleChildren = /* @__PURE__ */ new WeakSet(), this._styleAnchors = /* @__PURE__ */ new WeakMap(), this._ob = null, this.shadowRoot && n !== ld ? this._root = this.shadowRoot : e.shadowRoot === !1 ? this._root = this : (this.attachShadow(v({}, e.shadowRootOptions, { mode: "open" })), this._root = this.shadowRoot);
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
		this._connected = !1, vr(() => {
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
			if (n && !S(n)) for (let e in n) {
				let t = n[e];
				(t === Number || t && t.type === Number) && (e in this._props && (this._props[e] = me(this._props[e])), (i || (i = /* @__PURE__ */ Object.create(null)))[M(e)] = !0);
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
		if (t) for (let e in t) x(this, e) || Object.defineProperty(this, e, { get: () => En(t[e]) });
	}
	_resolveProps(e) {
		let { props: t } = e, n = S(t) ? t : Object.keys(t || {});
		for (let e of Object.keys(this)) e[0] !== "_" && n.includes(e) && this._setProp(e, this[e]);
		for (let e of n.map(M)) Object.defineProperty(this, e, {
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
		let t = this.hasAttribute(e), n = t ? this.getAttribute(e) : mu, r = M(e);
		t && this._numberProps && this._numberProps[r] && (n = me(n)), this._setProp(r, n, !1, !0);
	}
	_getProp(e) {
		return this._props[e];
	}
	_setProp(e, t, n = !0, r = !1) {
		if (t !== this._props[e] && (this._dirty = !0, t === mu ? delete this._props[e] : (this._props[e] = t, e === "key" && this._app && (this._app._ceVNode.key = t)), r && this._instance && this._update(), n)) {
			let n = this._ob;
			n && (this._processMutations(n.takeRecords()), n.disconnect()), t === !0 ? this.setAttribute(N(e), "") : typeof t == "string" || typeof t == "number" ? this.setAttribute(N(e), t + "") : t || this.removeAttribute(N(e)), n && n.observe(this, { attributes: !0 });
		}
	}
	_update() {
		let e = this._createVNode();
		this._app && (e.appContext = this._app._context), sd(e, this._root);
	}
	_createVNode() {
		let e = {};
		this.shadowRoot || (e.onVnodeMounted = e.onVnodeUpdated = this._renderSlots.bind(this));
		let t = W(this._def, v(e, this._props));
		return this._instance || (t.ce = (e) => {
			this._instance = e, e.ce = this, e.isCE = !0;
			let t = (e, t) => {
				this.dispatchEvent(new CustomEvent(e, j(t[0]) ? v({ detail: t }, t[0]) : { detail: t }));
			};
			e.emit = (e, ...n) => {
				t(e, n), N(e) !== e && t(N(e), n);
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
function yu(e) {
	let t = wc();
	return t && t.ce || null;
}
function bu() {
	let e = yu();
	return e && e.shadowRoot;
}
function xu(e = "$style") {
	{
		let t = wc();
		if (!t) return f;
		let n = t.type.__cssModules;
		return n && n[e] || f;
	}
}
var Su = /* @__PURE__ */ new WeakMap(), Cu = /* @__PURE__ */ new WeakMap(), wu = /* @__PURE__ */ Symbol("_moveCb"), Tu = /* @__PURE__ */ Symbol("_enterCb"), Eu = /* @__PURE__ */ ((e) => (delete e.props.mode, e))({
	name: "TransitionGroup",
	props: /* @__PURE__ */ v({}, gl, {
		tag: String,
		moveClass: String
	}),
	setup(e, { slots: t }) {
		let n = wc(), r = Si(), i, a;
		return La(() => {
			if (!i.length) return;
			let t = e.moveClass || `${e.name || "v"}-move`;
			if (!ju(i[0].el, n.vnode.el, t)) {
				i = [];
				return;
			}
			i.forEach(Du), i.forEach(Ou);
			let r = i.filter(ku);
			jl(n.vnode.el), r.forEach((e) => {
				let n = e.el, r = n.style;
				Cl(n, t), r.transform = r.webkitTransform = r.transitionDuration = "";
				let i = n[wu] = (e) => {
					e && e.target !== n || (!e || e.propertyName.endsWith("transform")) && (n.removeEventListener("transitionend", i), n[wu] = null, wl(n, t));
				};
				n.addEventListener("transitionend", i);
			}), i = [];
		}), () => {
			let o = /* @__PURE__ */ R(e), s = bl(o), c = o.tag || H;
			if (i = [], a) for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.el && t.el instanceof Element && !t.el[Pl] && (i.push(t), Ni(t, Ai(t, s, r, n)), Su.set(t, Au(t.el)));
			}
			a = t.default ? Pi(t.default()) : [];
			for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.key != null && Ni(t, Ai(t, s, r, n));
			}
			return W(c, null, a);
		};
	}
});
function Du(e) {
	let t = e.el;
	t[wu] && t[wu](), t[Tu] && t[Tu]();
}
function Ou(e) {
	Cu.set(e, Au(e.el));
}
function ku(e) {
	let t = Su.get(e), n = Cu.get(e), r = t.left - n.left, i = t.top - n.top;
	if (r || i) {
		let t = e.el, n = t.style, a = t.getBoundingClientRect(), o = 1, s = 1;
		return t.offsetWidth && (o = a.width / t.offsetWidth), t.offsetHeight && (s = a.height / t.offsetHeight), (!Number.isFinite(o) || o === 0) && (o = 1), (!Number.isFinite(s) || s === 0) && (s = 1), Math.abs(o - 1) < .01 && (o = 1), Math.abs(s - 1) < .01 && (s = 1), n.transform = n.webkitTransform = `translate(${r / o}px,${i / s}px)`, n.transitionDuration = "0s", e;
	}
}
function Au(e) {
	let t = e.getBoundingClientRect();
	return {
		left: t.left,
		top: t.top
	};
}
function ju(e, t, n) {
	let r = e.cloneNode(), i = e[ml];
	i && i.forEach((e) => {
		e.split(/\s+/).forEach((e) => e && r.classList.remove(e));
	}), n.split(/\s+/).forEach((e) => e && r.classList.add(e)), r.style.display = "none";
	let a = t.nodeType === 1 ? t : t.parentNode;
	a.appendChild(r);
	let { hasTransform: o } = Ol(r);
	return a.removeChild(r), o;
}
var Mu = (e) => {
	let t = e.props["onUpdate:modelValue"] || !1;
	return S(t) ? (e) => de(t, e) : t;
};
function Nu(e) {
	e.target.composing = !0;
}
function Pu(e) {
	let t = e.target;
	t.composing && (t.composing = !1, t.dispatchEvent(new Event("input")));
}
var Fu = /* @__PURE__ */ Symbol("_assign"), Iu = /* @__PURE__ */ Symbol("_initialValue");
function Lu(e, t, n) {
	return t && (e = e.trim()), n && (e = pe(e)), e;
}
var Ru = {
	created(e, { modifiers: { lazy: t, trim: n, number: r } }, i) {
		e.parentNode && (e.type === "text" ? e[Iu] = e.defaultValue.replace(/[\r\n]/g, "") : e.type === "textarea" && (e[Iu] = e.defaultValue.replace(/\r\n?/g, "\n"))), e[Fu] = Mu(i);
		let a = r || i.props && i.props.type === "number";
		$l(e, t ? "change" : "input", (t) => {
			t.target.composing || e[Fu](Lu(e.value, n, a));
		}), (n || a) && $l(e, "change", () => {
			e.value = Lu(e.value, n, a);
		}), t || ($l(e, "compositionstart", Nu), $l(e, "compositionend", Pu), $l(e, "change", Pu));
	},
	mounted(e, { value: t, modifiers: { trim: n, number: r } }) {
		let i = t ?? "", a = e[Iu];
		delete e[Iu], a !== void 0 && (e.type === "text" || e.type === "textarea") && e.value !== a ? e[Fu](Lu(e.value, n, r)) : e.value = i;
	},
	beforeUpdate(e, { value: t, oldValue: n, modifiers: { lazy: r, trim: i, number: a } }, o) {
		if (e[Fu] = Mu(o), e.composing) return;
		let s = (a || e.type === "number") && !/^0\d/.test(e.value) ? pe(e.value) : e.value, c = t ?? "";
		if (s === c) return;
		let l = e.getRootNode();
		(l instanceof Document || l instanceof ShadowRoot) && l.activeElement === e && e.type !== "range" && (r && t === n || i && e.value.trim() === c) || (e.value = c);
	}
}, zu = {
	deep: !0,
	created(e, t, n) {
		e[Fu] = Mu(n), $l(e, "change", () => {
			let t = e._modelValue, n = Gu(e), r = e.checked, i = e[Fu];
			if (S(t)) {
				let e = ze(t, n), a = e !== -1;
				if (r && !a) i(t.concat(n));
				else if (!r && a) {
					let n = [...t];
					n.splice(e, 1), i(n);
				}
			} else if (w(t)) {
				let e = new Set(t);
				r ? e.add(n) : e.delete(n), i(e);
			} else i(Ku(e, r));
		});
	},
	mounted: Bu,
	beforeUpdate(e, t, n) {
		e[Fu] = Mu(n), Bu(e, t, n);
	}
};
function Bu(e, { value: t, oldValue: n }, r) {
	e._modelValue = t;
	let i;
	if (S(t)) i = ze(t, r.props.value) > -1;
	else if (w(t)) i = t.has(r.props.value);
	else {
		if (t === n) return;
		i = Re(t, Ku(e, !0));
	}
	e.checked !== i && (e.checked = i);
}
var Vu = {
	created(e, { value: t }, n) {
		e.checked = Re(t, n.props.value), e[Fu] = Mu(n), $l(e, "change", () => {
			e[Fu](Gu(e));
		});
	},
	beforeUpdate(e, { value: t, oldValue: n }, r) {
		e[Fu] = Mu(r), t !== n && (e.checked = Re(t, r.props.value));
	}
}, Hu = {
	deep: !0,
	created(e, { value: t, modifiers: { number: n } }, r) {
		e._modelValue = t, $l(e, "change", () => {
			let t = Array.prototype.filter.call(e.options, (e) => e.selected).map((e) => n ? pe(Gu(e)) : Gu(e)), r = e.multiple, i = r ? w(e._modelValue) ? new Set(t) : t : t[0], a = e._pendingValue = [r, r ? S(i) ? t.slice() : t : i];
			try {
				e[Fu](i);
			} finally {
				vr(() => {
					e._pendingValue === a && (e._pendingValue = void 0);
				});
			}
		}), e[Fu] = Mu(r);
	},
	mounted(e, { value: t }) {
		Wu(e, t);
	},
	beforeUpdate(e, { value: t }, n) {
		e._modelValue = t, e[Fu] = Mu(n);
	},
	updated(e, { value: t }) {
		let n = e._pendingValue;
		e._pendingValue = void 0, (!n || n[0] !== e.multiple || !Uu(t, n[1], n[0])) && Wu(e, t);
	}
};
function Uu(e, t, n) {
	if (!n || S(e)) return Re(e, t);
	if (w(e)) {
		if (e.size !== t.length) return !1;
		for (let n of t) if (!e.has(n)) return !1;
		return !0;
	}
	return !1;
}
function Wu(e, t) {
	let n = e.multiple, r = S(t);
	if (!n || r || w(t)) {
		for (let i = 0, a = e.options.length; i < a; i++) {
			let a = e.options[i], o = Gu(a);
			if (n) {
				if (r) {
					let e = typeof o;
					a.selected = e === "string" || e === "number" ? t.some((e) => String(e) === String(o)) : ze(t, o) > -1;
				} else a.selected = t.has(o);
			} else if (Re(Gu(a), t)) {
				e.selectedIndex !== i && (e.selectedIndex = i);
				return;
			}
		}
		!n && e.selectedIndex !== -1 && (e.selectedIndex = -1);
	}
}
function Gu(e) {
	return "_value" in e ? e._value : e.value;
}
function Ku(e, t) {
	let n = t ? "_trueValue" : "_falseValue";
	return n in e ? e[n] : t;
}
var qu = {
	created(e, t, n) {
		Yu(e, t, n, null, "created");
	},
	mounted(e, t, n) {
		Yu(e, t, n, null, "mounted");
	},
	beforeUpdate(e, t, n, r) {
		Yu(e, t, n, r, "beforeUpdate");
	},
	updated(e, t, n, r) {
		Yu(e, t, n, r, "updated");
	}
};
function Ju(e, t) {
	switch (e) {
		case "SELECT": return Hu;
		case "TEXTAREA": return Ru;
		default: switch (t) {
			case "checkbox": return zu;
			case "radio": return Vu;
			default: return Ru;
		}
	}
}
function Yu(e, t, n, r, i) {
	let a = Ju(e.tagName, n.props && n.props.type)[i];
	a && a(e, t, n, r);
}
function Xu() {
	Ru.getSSRProps = ({ value: e }) => ({ value: e }), Vu.getSSRProps = ({ value: e }, t) => {
		if (t.props && Re(t.props.value, e)) return { checked: !0 };
	}, zu.getSSRProps = ({ value: e }, t) => {
		if (S(e)) {
			if (t.props && ze(e, t.props.value) > -1) return { checked: !0 };
		} else if (w(e)) {
			if (t.props && e.has(t.props.value)) return { checked: !0 };
		} else if (e) return { checked: !0 };
	}, qu.getSSRProps = (e, t) => {
		if (typeof t.type != "string") return;
		let n = Ju(t.type.toUpperCase(), t.props && t.props.type);
		if (n.getSSRProps) return n.getSSRProps(e, t);
	};
}
var Zu = [
	"ctrl",
	"shift",
	"alt",
	"meta"
], Qu = {
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
	exact: (e, t) => Zu.some((n) => e[`${n}Key`] && !t.includes(n))
}, $u = (e, t) => {
	if (!e) return e;
	let n = e._withMods || (e._withMods = {}), r = t.join(".");
	return n[r] || (n[r] = ((n, ...r) => {
		for (let e = 0; e < t.length; e++) {
			let r = Qu[t[e]];
			if (r && r(n, t)) return;
		}
		return e(n, ...r);
	}));
}, ed = {
	esc: "escape",
	space: " ",
	up: "arrow-up",
	left: "arrow-left",
	right: "arrow-right",
	down: "arrow-down",
	delete: "backspace"
}, td = (e, t) => {
	let n = e._withKeys || (e._withKeys = {}), r = t.join(".");
	return n[r] || (n[r] = ((n) => {
		if (!("key" in n)) return;
		let r = N(n.key);
		if (t.some((e) => e === r || ed[e] === r)) return e(n);
	}));
}, nd = /* @__PURE__ */ v({ patchProp: du }, dl), rd, id = !1;
function ad() {
	return rd || (rd = Ss(nd));
}
function od() {
	return rd = id ? rd : Cs(nd), id = !0, rd;
}
var sd = ((...e) => {
	ad().render(...e);
}), cd = ((...e) => {
	od().hydrate(...e);
}), ld = ((...e) => {
	let t = ad().createApp(...e), { mount: n } = t;
	return t.mount = (e) => {
		let r = fd(e);
		if (!r) return;
		let i = t._component;
		!D(i) && !i.render && !i.template && (i.template = r.innerHTML), r.nodeType === 1 && (r.textContent = "");
		let a = n(r, !1, dd(r));
		return r instanceof Element && (r.removeAttribute("v-cloak"), r.setAttribute("data-v-app", "")), a;
	}, t;
}), ud = ((...e) => {
	let t = od().createApp(...e), { mount: n } = t;
	return t.mount = (e) => {
		let t = fd(e);
		if (t) return n(t, !0, dd(t));
	}, t;
});
function dd(e) {
	if (e instanceof SVGElement) return "svg";
	if (typeof MathMLElement == "function" && e instanceof MathMLElement) return "mathml";
}
function fd(e) {
	return O(e) ? document.querySelector(e) : e;
}
var pd = !1, md = () => {
	pd || (pd = !0, Xu(), Ll());
}, hd = /* @__PURE__ */ c({
	BaseTransition: () => Oi,
	BaseTransitionPropsValidators: () => wi,
	Comment: () => U,
	DeprecationTypes: () => null,
	EffectScope: () => Ge,
	ErrorCodes: () => ar,
	ErrorTypeStrings: () => el,
	Fragment: () => H,
	KeepAlive: () => wa,
	ReactiveEffect: () => Xe,
	Static: () => qs,
	Suspense: () => Fs,
	Teleport: () => _i,
	Text: () => Ks,
	TrackOpTypes: () => zn,
	Transition: () => _l,
	TransitionGroup: () => Eu,
	TriggerOpTypes: () => Bn,
	VueElement: () => vu,
	assertNumber: () => ir,
	callWithAsyncErrorHandling: () => cr,
	callWithErrorHandling: () => sr,
	camelize: () => M,
	capitalize: () => le,
	cloneVNode: () => dc,
	compatUtils: () => null,
	compile: () => gd,
	computed: () => qc,
	createApp: () => ld,
	createBlock: () => nc,
	createCommentVNode: () => mc,
	createElementBlock: () => tc,
	createElementVNode: () => cc,
	createHydrationRenderer: () => Cs,
	createPropsRestProxy: () => So,
	createRenderer: () => Ss,
	createSSRApp: () => ud,
	createSlots: () => $a,
	createStaticVNode: () => pc,
	createTextVNode: () => fc,
	createVNode: () => W,
	customRef: () => jn,
	defineAsyncComponent: () => xa,
	defineComponent: () => Fi,
	defineCustomElement: () => hu,
	defineEmits: () => lo,
	defineExpose: () => uo,
	defineModel: () => mo,
	defineOptions: () => fo,
	defineProps: () => co,
	defineSSRCustomElement: () => gu,
	defineSlots: () => po,
	devtools: () => tl,
	effect: () => lt,
	effectScope: () => Ke,
	getCurrentInstance: () => wc,
	getCurrentScope: () => qe,
	getCurrentWatcher: () => Wn,
	getTransitionRawChildren: () => Pi,
	guardReactiveProps: () => uc,
	h: () => Jc,
	handleError: () => lr,
	hasInjectionContext: () => Xr,
	hydrate: () => cd,
	hydrateOnIdle: () => ma,
	hydrateOnInteraction: () => va,
	hydrateOnMediaQuery: () => _a,
	hydrateOnVisible: () => ga,
	initCustomFormatter: () => Yc,
	initDirectivesForSSR: () => md,
	inject: () => Yr,
	isMemoSame: () => Zc,
	isProxy: () => _n,
	isReactive: () => mn,
	isReadonly: () => hn,
	isRef: () => z,
	isRuntimeOnly: () => Lc,
	isShallow: () => gn,
	isVNode: () => rc,
	markRaw: () => vn,
	mergeDefaults: () => bo,
	mergeModels: () => xo,
	mergeProps: () => vc,
	nextTick: () => vr,
	nodeOps: () => dl,
	normalizeClass: () => Ce,
	normalizeProps: () => we,
	normalizeStyle: () => ve,
	onActivated: () => Ea,
	onBeforeMount: () => Pa,
	onBeforeUnmount: () => Ra,
	onBeforeUpdate: () => Ia,
	onDeactivated: () => Da,
	onErrorCaptured: () => Ua,
	onMounted: () => Fa,
	onRenderTracked: () => Ha,
	onRenderTriggered: () => Va,
	onScopeDispose: () => Je,
	onServerPrefetch: () => Ba,
	onUnmounted: () => za,
	onUpdated: () => La,
	onWatcherCleanup: () => Gn,
	openBlock: () => Xs,
	patchProp: () => du,
	popScopeId: () => Ur,
	provide: () => Jr,
	proxyRefs: () => kn,
	pushScopeId: () => Hr,
	queuePostFlushCb: () => Sr,
	reactive: () => ln,
	readonly: () => dn,
	ref: () => xn,
	registerRuntimeCompiler: () => Ic,
	render: () => sd,
	renderList: () => Qa,
	renderSlot: () => eo,
	resolveComponent: () => Ka,
	resolveDirective: () => Ya,
	resolveDynamicComponent: () => Ja,
	resolveFilter: () => null,
	resolveTransitionHooks: () => Ai,
	setBlockTracking: () => $s,
	setDevtoolsHook: () => nl,
	setTransitionHooks: () => Ni,
	shallowReactive: () => un,
	shallowReadonly: () => fn,
	shallowRef: () => Sn,
	ssrContextKey: () => Zr,
	ssrUtils: () => rl,
	stop: () => ut,
	toDisplayString: () => Ve,
	toHandlerKey: () => ue,
	toHandlers: () => no,
	toRaw: () => R,
	toRef: () => Fn,
	toRefs: () => Mn,
	toValue: () => Dn,
	transformVNodeArgs: () => ac,
	triggerRef: () => Tn,
	unref: () => En,
	useAttrs: () => _o,
	useCssModule: () => xu,
	useCssVars: () => zl,
	useHost: () => yu,
	useId: () => Ii,
	useModel: () => Uo,
	useSSRContext: () => Qr,
	useShadowRoot: () => bu,
	useSlots: () => go,
	useTemplateRef: () => Ri,
	useTransitionState: () => Si,
	vModelCheckbox: () => zu,
	vModelDynamic: () => qu,
	vModelRadio: () => Vu,
	vModelSelect: () => Hu,
	vModelText: () => Ru,
	vShow: () => Fl,
	version: () => Qc,
	warn: () => $c,
	watch: () => ni,
	watchEffect: () => $r,
	watchPostEffect: () => ei,
	watchSyncEffect: () => ti,
	withAsyncContext: () => Co,
	withCtx: () => Gr,
	withDefaults: () => ho,
	withDirectives: () => Kr,
	withKeys: () => td,
	withMemo: () => Xc,
	withModifiers: () => $u,
	withScopeId: () => Wr
}), gd = () => {};
//#endregion
//#region node_modules/@intlify/shared/dist/shared.mjs
function _d(e, t) {
	typeof console < "u" && (console.warn("[intlify] " + e), t && console.warn(t.stack));
}
var vd = typeof window < "u", yd = (e, t = !1) => t ? Symbol.for(e) : Symbol(e), bd = (e, t, n) => xd({
	l: e,
	k: t,
	s: n
}), xd = (e) => JSON.stringify(e).replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029").replace(/\u0027/g, "\\u0027"), G = (e) => typeof e == "number" && isFinite(e), Sd = (e) => Id(e) === "[object Date]", Cd = (e) => Id(e) === "[object RegExp]", wd = (e) => Q(e) && Object.keys(e).length === 0, Td = Object.assign, Ed = Object.create, K = (e = null) => Ed(e), Dd, Od = () => Dd || (Dd = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : K());
function kd(e) {
	return e.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;").replace(/\//g, "&#x2F;").replace(/=/g, "&#x3D;");
}
function Ad(e) {
	return e.replace(/&(?![a-zA-Z0-9#]{2,6};)/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&apos;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function jd(e) {
	return e = e.replace(/(\w+)\s*=\s*"([^"]*)"/g, (e, t, n) => `${t}="${Ad(n)}"`), e = e.replace(/(\w+)\s*=\s*'([^']*)'/g, (e, t, n) => `${t}='${Ad(n)}'`), /\s*on\w+\s*=\s*["']?[^"'>]+["']?/gi.test(e) && (e = e.replace(/(\s+)(on)(\w+\s*=)/gi, "$1&#111;n$3")), [/(\s+(?:href|src|action|formaction)\s*=\s*["']?)\s*javascript:/gi, /(style\s*=\s*["'][^"']*url\s*\(\s*)javascript:/gi].forEach((t) => {
		e = e.replace(t, "$1javascript&#58;");
	}), e;
}
var Md = Object.prototype.hasOwnProperty;
function Nd(e, t) {
	return Md.call(e, t);
}
var q = Array.isArray, J = (e) => typeof e == "function", Y = (e) => typeof e == "string", X = (e) => typeof e == "boolean", Z = (e) => typeof e == "object" && !!e, Pd = (e) => Z(e) && J(e.then) && J(e.catch), Fd = Object.prototype.toString, Id = (e) => Fd.call(e), Q = (e) => Id(e) === "[object Object]", Ld = (e) => e == null ? "" : q(e) || Q(e) && e.toString === Fd ? JSON.stringify(e, null, 2) : String(e);
function Rd(e, t = "") {
	return e.reduce((e, n, r) => r === 0 ? e + n : e + t + n, "");
}
function zd() {
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
var Bd = (e) => !Z(e) || q(e);
function Vd(e, t) {
	if (Bd(e) || Bd(t)) throw Error("Invalid value");
	let n = [{
		src: e,
		des: t
	}];
	for (; n.length;) {
		let { src: e, des: t } = n.pop();
		Object.keys(e).forEach((r) => {
			r !== "__proto__" && (Z(e[r]) && !Z(t[r]) && (t[r] = Array.isArray(e[r]) ? [] : K()), Bd(t[r]) || Bd(e[r]) ? t[r] = e[r] : n.push({
				src: e[r],
				des: t[r]
			}));
		});
	}
}
//#endregion
//#region node_modules/@intlify/message-compiler/dist/message-compiler.mjs
function Hd(e, t, n) {
	return {
		line: e,
		column: t,
		offset: n
	};
}
function Ud(e, t, n) {
	let r = {
		start: e,
		end: t
	};
	return n != null && (r.source = n), r;
}
var $ = {
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
$.EXPECTED_TOKEN, $.INVALID_TOKEN_IN_PLACEHOLDER, $.UNTERMINATED_SINGLE_QUOTE_IN_PLACEHOLDER, $.UNKNOWN_ESCAPE_SEQUENCE, $.INVALID_UNICODE_ESCAPE_SEQUENCE, $.UNBALANCED_CLOSING_BRACE, $.UNTERMINATED_CLOSING_BRACE, $.EMPTY_PLACEHOLDER, $.NOT_ALLOW_NEST_PLACEHOLDER, $.INVALID_LINKED_FORMAT, $.MUST_HAVE_MESSAGES_IN_PLURAL, $.UNEXPECTED_EMPTY_LINKED_MODIFIER, $.UNEXPECTED_EMPTY_LINKED_KEY, $.UNEXPECTED_LEXICAL_ANALYSIS, $.UNHANDLED_CODEGEN_NODE_TYPE, $.UNHANDLED_MINIFIER_NODE_TYPE;
function Wd(e, t, n = {}) {
	let { domain: r, messages: i, args: a } = n, o = SyntaxError(String(e));
	return o.code = e, t && (o.location = t), o.domain = r, o;
}
function Gd(e) {
	throw e;
}
var Kd = " ", qd = "\r", Jd = "\n", Yd = "\u2028", Xd = "\u2029";
function Zd(e) {
	let t = e, n = 0, r = 1, i = 1, a = 0, o = (e) => t[e] === qd && t[e + 1] === Jd, s = (e) => t[e] === Jd, c = (e) => t[e] === Xd, l = (e) => t[e] === Yd, u = (e) => o(e) || s(e) || c(e) || l(e), d = () => n, f = () => r, p = () => i, m = () => a, h = (e) => o(e) || c(e) || l(e) ? Jd : t[e], g = () => h(n), _ = () => h(n + a);
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
var Qd = void 0, $d = "'", ef = "tokenizer";
function tf(e, t = {}) {
	let n = t.location !== !1, r = Zd(e), i = () => r.index(), a = () => Hd(r.line(), r.column(), r.index()), o = a(), s = i(), c = {
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
			let r = Wd(e, n ? Ud(a.startLoc, t) : null, {
				domain: ef,
				args: i
			});
			u(r);
		}
	}
	function f(e, t, r) {
		e.endLoc = a(), e.currentType = t;
		let i = { type: t };
		return n && (i.loc = Ud(e.startLoc, e.endLoc)), r != null && (i.value = r), i;
	}
	let p = (e) => f(e, 13);
	function m(e, t) {
		return e.currentChar() === t ? (e.next(), t) : (d($.EXPECTED_TOKEN, a(), 0, t), "");
	}
	function h(e) {
		let t = "";
		for (; e.currentPeek() === Kd || e.currentPeek() === Jd;) t += e.currentPeek(), e.peek();
		return t;
	}
	function g(e) {
		let t = h(e);
		return e.skipToPeek(), t;
	}
	function _(e) {
		if (e === Qd) return !1;
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t === 95;
	}
	function v(e) {
		if (e === Qd) return !1;
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
		let r = e.currentPeek() === $d;
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
			return t === "{" ? _(e.peek()) : t === "@" || t === "|" || t === ":" || t === "." || t === Kd || !t ? !1 : t === Jd ? (e.peek(), r()) : D(e, !1);
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
			return i === "{" || i === "@" || !i ? t : i === "|" ? r !== Kd && r !== Jd : i === Kd ? (e.peek(), n(!0, Kd)) : i !== Jd || (e.peek(), n(!0, Jd));
		}, r = n();
		return t && e.resetPeek(), r;
	}
	function O(e, t) {
		let n = e.currentChar();
		if (n !== Qd) return t(n) ? (e.next(), n) : null;
	}
	function k(e) {
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t >= 48 && t <= 57 || t === 95 || t === 36;
	}
	function A(e) {
		return O(e, k);
	}
	function ee(e) {
		let t = e.charCodeAt(0);
		return t >= 97 && t <= 122 || t >= 65 && t <= 90 || t >= 48 && t <= 57 || t === 95 || t === 36 || t === 45;
	}
	function te(e) {
		return O(e, ee);
	}
	function ne(e) {
		let t = e.charCodeAt(0);
		return t >= 48 && t <= 57;
	}
	function re(e) {
		return O(e, ne);
	}
	function j(e) {
		let t = e.charCodeAt(0);
		return t >= 48 && t <= 57 || t >= 65 && t <= 70 || t >= 97 && t <= 102;
	}
	function ie(e) {
		return O(e, j);
	}
	function ae(e) {
		let t = "", n = "";
		for (; t = re(e);) n += t;
		return n;
	}
	function oe(e) {
		let t = "";
		for (;;) {
			let n = e.currentChar();
			if (n === "\\") {
				let r = e.peek();
				r === "{" || r === "}" || r === "@" || r === "|" || r === "\\" ? (t += n + r, e.next(), e.next()) : (e.resetPeek(), t += n, e.next());
			} else if (n === "{" || n === "}" || n === "@" || n === "|" || !n) break;
			else if (n === Kd || n === Jd) {
				if (D(e)) t += n, e.next();
				else if (E(e)) break;
				else t += n, e.next();
			} else t += n, e.next();
		}
		return t;
	}
	function se(e) {
		g(e);
		let t = "", n = "";
		for (; t = te(e);) n += t;
		let r = e.currentChar();
		if (r && r !== "}" && r !== Qd && r !== Kd && r !== Jd && r !== "　") {
			let t = de(e);
			return d($.INVALID_TOKEN_IN_PLACEHOLDER, a(), 0, n + t), n + t;
		}
		return e.currentChar() === Qd && d($.UNTERMINATED_CLOSING_BRACE, a(), 0), n;
	}
	function M(e) {
		g(e);
		let t = "";
		return e.currentChar() === "-" ? (e.next(), t += `-${ae(e)}`) : t += ae(e), e.currentChar() === Qd && d($.UNTERMINATED_CLOSING_BRACE, a(), 0), t;
	}
	function ce(e) {
		return e !== $d && e !== Jd;
	}
	function N(e) {
		g(e), m(e, "'");
		let t = "", n = "";
		for (; t = O(e, ce);) n += t === "\\" ? le(e) : t;
		let r = e.currentChar();
		return r === Jd || r === Qd ? (d($.UNTERMINATED_SINGLE_QUOTE_IN_PLACEHOLDER, a(), 0), r === Jd && (e.next(), m(e, "'")), n) : (m(e, "'"), n);
	}
	function le(e) {
		let t = e.currentChar();
		switch (t) {
			case "\\":
			case "'": return e.next(), `\\${t}`;
			case "u": return ue(e, t, 4);
			case "U": return ue(e, t, 6);
			default: return d($.UNKNOWN_ESCAPE_SEQUENCE, a(), 0, t), "";
		}
	}
	function ue(e, t, n) {
		m(e, t);
		let r = "";
		for (let i = 0; i < n; i++) {
			let n = ie(e);
			if (!n) {
				d($.INVALID_UNICODE_ESCAPE_SEQUENCE, a(), 0, `\\${t}${r}${e.currentChar()}`);
				break;
			}
			r += n;
		}
		return `\\${t}${r}`;
	}
	function P(e) {
		return e !== "{" && e !== "}" && e !== Kd && e !== Jd;
	}
	function de(e) {
		g(e);
		let t = "", n = "";
		for (; t = O(e, P);) n += t;
		return n;
	}
	function fe(e) {
		let t = "", n = "";
		for (; t = A(e);) n += t;
		return n;
	}
	function pe(e) {
		let t = (n) => {
			let r = e.currentChar();
			return r === "{" || r === "@" || r === "|" || r === "(" || r === ")" || !r || r === Kd ? n : (n += r, e.next(), t(n));
		};
		return t("");
	}
	function me(e) {
		g(e);
		let t = m(e, "|");
		return g(e), t;
	}
	function he(e, t) {
		let n = null;
		switch (e.currentChar()) {
			case "{": return t.braceNest >= 1 && d($.NOT_ALLOW_NEST_PLACEHOLDER, a(), 0), e.next(), n = f(t, 2, "{"), g(e), t.braceNest++, n;
			case "}": return t.braceNest > 0 && t.currentType === 2 && d($.EMPTY_PLACEHOLDER, a(), 0), e.next(), n = f(t, 3, "}"), t.braceNest--, t.braceNest > 0 && g(e), t.inLinked && t.braceNest === 0 && (t.inLinked = !1), n;
			case "@": return t.braceNest > 0 && d($.UNTERMINATED_CLOSING_BRACE, a(), 0), n = ge(e, t) || p(t), t.braceNest = 0, n;
			default: {
				let r = !0, i = !0, o = !0;
				if (E(e)) return t.braceNest > 0 && d($.UNTERMINATED_CLOSING_BRACE, a(), 0), n = f(t, 1, me(e)), t.braceNest = 0, t.inLinked = !1, n;
				if (t.braceNest > 0 && (t.currentType === 4 || t.currentType === 5 || t.currentType === 6)) return d($.UNTERMINATED_CLOSING_BRACE, a(), 0), t.braceNest = 0, _e(e, t);
				if (r = y(e, t)) return n = f(t, 4, se(e)), g(e), n;
				if (i = b(e, t)) return n = f(t, 5, M(e)), g(e), n;
				if (o = x(e, t)) return n = f(t, 6, N(e)), g(e), n;
				if (!r && !i && !o) return n = f(t, 12, de(e)), d($.INVALID_TOKEN_IN_PLACEHOLDER, a(), 0, n.value), g(e), n;
				break;
			}
		}
		return n;
	}
	function ge(e, t) {
		let { currentType: n } = t, r = null, i = e.currentChar();
		switch ((n === 7 || n === 8 || n === 11 || n === 9) && (i === Jd || i === Kd) && d($.INVALID_LINKED_FORMAT, a(), 0), i) {
			case "@": return e.next(), r = f(t, 7, "@"), t.inLinked = !0, r;
			case ".": return g(e), e.next(), f(t, 8, ".");
			case ":": return g(e), e.next(), f(t, 9, ":");
			default: return E(e) ? (r = f(t, 1, me(e)), t.braceNest = 0, t.inLinked = !1, r) : S(e, t) || w(e, t) ? (g(e), ge(e, t)) : C(e, t) ? (g(e), f(t, 11, fe(e))) : T(e, t) ? (g(e), i === "{" ? he(e, t) || r : f(t, 10, pe(e))) : (n === 7 && d($.INVALID_LINKED_FORMAT, a(), 0), t.braceNest = 0, t.inLinked = !1, _e(e, t));
		}
	}
	function _e(e, t) {
		let n = { type: 13 };
		if (t.braceNest > 0) return he(e, t) || p(t);
		if (t.inLinked) return ge(e, t) || p(t);
		switch (e.currentChar()) {
			case "{": return he(e, t) || p(t);
			case "}": return d($.UNBALANCED_CLOSING_BRACE, a(), 0), e.next(), f(t, 3, "}");
			case "@": return ge(e, t) || p(t);
			default:
				if (E(e)) return n = f(t, 1, me(e)), t.braceNest = 0, t.inLinked = !1, n;
				if (D(e)) return f(t, 0, oe(e));
		}
		return n;
	}
	function ve() {
		let { currentType: e, offset: t, startLoc: n, endLoc: o } = c;
		return c.lastType = e, c.lastOffset = t, c.lastStartLoc = n, c.lastEndLoc = o, c.offset = i(), c.startLoc = a(), r.currentChar() === Qd ? f(c, 13) : _e(r, c);
	}
	return {
		nextToken: ve,
		currentOffset: i,
		currentPosition: a,
		context: l
	};
}
var nf = "parser", rf = /(?:\\\\|\\'|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{6}))/g, af = /\\([\\@{}|])/g;
function of(e, t) {
	return t;
}
function sf(e, t, n) {
	switch (e) {
		case "\\\\": return "\\";
		case "\\'": return "'";
		default: {
			let e = parseInt(t || n, 16);
			return e <= 55295 || e >= 57344 ? String.fromCodePoint(e) : "�";
		}
	}
}
function cf(e = {}) {
	let t = e.location !== !1, { onError: n } = e;
	function r(e, r, i, a, ...o) {
		let s = e.currentPosition();
		if (s.offset += a, s.column += a, n) {
			let e = Wd(r, t ? Ud(i, s) : null, {
				domain: nf,
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
		return r.value = t.replace(af, of), a(r, e.currentOffset(), e.currentPosition()), r;
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
		return o.value = t.replace(rf, sf), e.nextToken(), a(o, e.currentOffset(), e.currentPosition()), o;
	}
	function u(e) {
		let t = e.nextToken(), n = e.context(), { lastOffset: o, lastStartLoc: s } = n, c = i(8, o, s);
		return t.type === 11 ? (t.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, n.lastStartLoc, 0, lf(t)), c.value = t.value || "", a(c, e.currentOffset(), e.currentPosition()), { node: c }) : (r(e, $.UNEXPECTED_EMPTY_LINKED_MODIFIER, n.lastStartLoc, 0), c.value = "", a(c, o, s), {
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
		switch (o.type !== 9 && r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(o)), o = e.nextToken(), o.type === 2 && (o = e.nextToken()), o.type) {
			case 10:
				o.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(o)), n.key = d(e, o.value || "");
				break;
			case 4:
				o.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(o)), n.key = c(e, o.value || "");
				break;
			case 5:
				o.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(o)), n.key = s(e, o.value || "");
				break;
			case 6:
				o.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(o)), n.key = l(e, o.value || "");
				break;
			default: {
				r(e, $.UNEXPECTED_EMPTY_LINKED_KEY, t.lastStartLoc, 0);
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
					i.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(i)), n.items.push(o(e, i.value || ""));
					break;
				case 5:
					i.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(i)), n.items.push(s(e, i.value || ""));
					break;
				case 4:
					i.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(i)), n.items.push(c(e, i.value || ""));
					break;
				case 6:
					i.value ?? r(e, $.UNEXPECTED_LEXICAL_ANALYSIS, t.lastStartLoc, 0, lf(i)), n.items.push(l(e, i.value || ""));
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
		return c && r(e, $.MUST_HAVE_MESSAGES_IN_PLURAL, n, 0), a(l, e.currentOffset(), e.currentPosition()), l;
	}
	function h(e) {
		let t = e.context(), { offset: n, startLoc: r } = t, i = p(e);
		return t.currentType === 13 ? i : m(e, n, r, i);
	}
	function g(n) {
		let o = tf(n, Td({}, e)), s = o.context(), c = i(0, s.offset, s.startLoc);
		return t && c.loc && (c.loc.source = n), c.body = h(o), e.onCacheKey && (c.cacheKey = e.onCacheKey(n)), s.currentType !== 13 && r(o, $.UNEXPECTED_LEXICAL_ANALYSIS, s.lastStartLoc, 0, n[s.offset] || ""), a(c, o.currentOffset(), o.currentPosition()), c;
	}
	return { parse: g };
}
function lf(e) {
	if (e.type === 13) return "EOF";
	let t = (e.value || "").replace(/\r?\n/gu, "\\n");
	return t.length > 10 ? t.slice(0, 9) + "…" : t;
}
function uf(e, t = {}) {
	let n = {
		ast: e,
		helpers: /* @__PURE__ */ new Set()
	};
	return {
		context: () => n,
		helper: (e) => (n.helpers.add(e), e)
	};
}
function df(e, t) {
	for (let n = 0; n < e.length; n++) ff(e[n], t);
}
function ff(e, t) {
	switch (e.type) {
		case 1:
			df(e.cases, t), t.helper("plural");
			break;
		case 2:
			df(e.items, t);
			break;
		case 6:
			ff(e.key, t), t.helper("linked"), t.helper("type");
			break;
		case 5:
			t.helper("interpolate"), t.helper("list");
			break;
		case 4: t.helper("interpolate"), t.helper("named");
	}
}
function pf(e, t = {}) {
	let n = uf(e);
	n.helper("normalize"), e.body && ff(e.body, n);
	let r = n.context();
	e.helpers = Array.from(r.helpers);
}
function mf(e) {
	let t = e.body;
	return t.type === 2 ? hf(t) : t.cases.forEach((e) => hf(e)), e;
}
function hf(e) {
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
			e.static = Rd(t);
			for (let t = 0; t < e.items.length; t++) {
				let n = e.items[t];
				(n.type === 3 || n.type === 9) && delete n.value;
			}
		}
	}
}
function gf(e) {
	switch (e.t = e.type, e.type) {
		case 0: {
			let t = e;
			gf(t.body), t.b = t.body, delete t.body;
			break;
		}
		case 1: {
			let t = e, n = t.cases;
			for (let e = 0; e < n.length; e++) gf(n[e]);
			t.c = n, delete t.cases;
			break;
		}
		case 2: {
			let t = e, n = t.items;
			for (let e = 0; e < n.length; e++) gf(n[e]);
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
			gf(t.key), t.k = t.key, delete t.key, t.modifier && (gf(t.modifier), t.m = t.modifier, delete t.modifier);
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
function _f(e, t) {
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
function vf(e, t) {
	let { helper: n } = e;
	e.push(`${n("linked")}(`), Sf(e, t.key), t.modifier ? (e.push(", "), Sf(e, t.modifier), e.push(", _type")) : e.push(", undefined, _type"), e.push(")");
}
function yf(e, t) {
	let { helper: n, needIndent: r } = e;
	e.push(`${n("normalize")}([`), e.indent(r());
	let i = t.items.length;
	for (let n = 0; n < i && (Sf(e, t.items[n]), n !== i - 1); n++) e.push(", ");
	e.deindent(r()), e.push("])");
}
function bf(e, t) {
	let { helper: n, needIndent: r } = e;
	if (t.cases.length > 1) {
		e.push(`${n("plural")}([`), e.indent(r());
		let i = t.cases.length;
		for (let n = 0; n < i && (Sf(e, t.cases[n]), n !== i - 1); n++) e.push(", ");
		e.deindent(r()), e.push("])");
	}
}
function xf(e, t) {
	t.body ? Sf(e, t.body) : e.push("null");
}
function Sf(e, t) {
	let { helper: n } = e;
	switch (t.type) {
		case 0:
			xf(e, t);
			break;
		case 1:
			bf(e, t);
			break;
		case 2:
			yf(e, t);
			break;
		case 6:
			vf(e, t);
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
var Cf = (e, t = {}) => {
	let n = Y(t.mode) ? t.mode : "normal", r = Y(t.filename) ? t.filename : "message.intl", i = !!t.sourceMap, a = t.breakLineCode == null ? n === "arrow" ? ";" : "\n" : t.breakLineCode, o = t.needIndent ? t.needIndent : n !== "arrow", s = e.helpers || [], c = _f(e, {
		mode: n,
		filename: r,
		sourceMap: i,
		breakLineCode: a,
		needIndent: o
	});
	c.push(n === "normal" ? "function __msg__ (ctx) {" : "(ctx) => {"), c.indent(o), s.length > 0 && (c.push(`const { ${Rd(s.map((e) => `${e}: _${e}`), ", ")} } = ctx`), c.newline()), c.push("return "), Sf(c, e), c.deindent(o), c.push("}"), delete e.helpers;
	let { code: l, map: u } = c.context();
	return {
		ast: e,
		code: l,
		map: u ? u.toJSON() : void 0
	};
};
function wf(e, t = {}) {
	let n = Td({}, t), r = !!n.jit, i = !!n.minify, a = n.optimize == null || n.optimize, o = cf(n).parse(e);
	return r ? (a && mf(o), i && gf(o), {
		ast: o,
		code: ""
	}) : (pf(o, n), Cf(o, n));
}
//#endregion
//#region node_modules/@intlify/core-base/dist/core-base.mjs
function Tf() {
	typeof __INTLIFY_DROP_MESSAGE_COMPILER__ != "boolean" && (Od().__INTLIFY_DROP_MESSAGE_COMPILER__ = !1);
}
function Ef(e) {
	return Z(e) && If(e) === 0 && (Nd(e, "b") || Nd(e, "body"));
}
var Df = ["b", "body"];
function Of(e) {
	return Uf(e, Df);
}
var kf = ["c", "cases"];
function Af(e) {
	return Uf(e, kf, []);
}
var jf = ["s", "static"];
function Mf(e) {
	return Uf(e, jf);
}
var Nf = ["i", "items"];
function Pf(e) {
	return Uf(e, Nf, []);
}
var Ff = ["t", "type"];
function If(e) {
	return Uf(e, Ff);
}
var Lf = ["v", "value"];
function Rf(e, t) {
	let n = Uf(e, Lf);
	if (n != null) return n;
	throw Gf(t);
}
var zf = ["m", "modifier"];
function Bf(e) {
	return Uf(e, zf);
}
var Vf = ["k", "key"];
function Hf(e) {
	let t = Uf(e, Vf);
	if (t) return t;
	throw Gf(6);
}
function Uf(e, t, n) {
	for (let n = 0; n < t.length; n++) {
		let r = t[n];
		if (Nd(e, r) && e[r] != null) return e[r];
	}
	return n;
}
var Wf = [
	...Df,
	...kf,
	...jf,
	...Nf,
	...Vf,
	...zf,
	...Lf,
	...Ff
];
function Gf(e) {
	return /* @__PURE__ */ Error(`unhandled node type: ${e}`);
}
function Kf(e) {
	return (t) => qf(t, e);
}
function qf(e, t) {
	let n = Of(t);
	if (n == null) throw Gf(0);
	if (If(n) === 1) {
		let t = Af(n);
		return e.plural(t.reduce((t, n) => [...t, Jf(e, n)], []));
	}
	return Jf(e, n);
}
function Jf(e, t) {
	let n = Mf(t);
	if (n != null) return e.type === "text" ? n : e.normalize([n]);
	{
		let n = Pf(t).reduce((t, n) => [...t, Yf(e, n)], []);
		return e.normalize(n);
	}
}
function Yf(e, t) {
	let n = If(t);
	switch (n) {
		case 3: return Rf(t, n);
		case 9: return Rf(t, n);
		case 4: {
			let r = t;
			if (Nd(r, "k") && r.k) return e.interpolate(e.named(r.k));
			if (Nd(r, "key") && r.key) return e.interpolate(e.named(r.key));
			throw Gf(n);
		}
		case 5: {
			let r = t;
			if (Nd(r, "i") && G(r.i)) return e.interpolate(e.list(r.i));
			if (Nd(r, "index") && G(r.index)) return e.interpolate(e.list(r.index));
			throw Gf(n);
		}
		case 6: {
			let n = t, r = Bf(n), i = Hf(n);
			return e.linked(Yf(e, i), r ? Yf(e, r) : void 0, e.type);
		}
		case 7: return Rf(t, n);
		case 8: return Rf(t, n);
		default: throw Error(`unhandled node on format message part: ${n}`);
	}
}
var Xf = (e) => e, Zf = K();
function Qf(e, t = {}) {
	let n = !1, r = t.onError || Gd;
	return t.onError = (e) => {
		n = !0, r(e);
	}, {
		...wf(e, t),
		detectError: n
	};
}
/* #__NO_SIDE_EFFECTS__ */
function $f(e, t) {
	if (!__INTLIFY_DROP_MESSAGE_COMPILER__ && Y(e)) {
		X(t.warnHtmlMessage) && t.warnHtmlMessage;
		let n = (t.onCacheKey || Xf)(e), r = Zf[n];
		if (r) return r;
		let { ast: i, detectError: a } = Qf(e, {
			...t,
			location: !1,
			jit: !0
		}), o = Kf(i);
		return a ? o : Zf[n] = o;
	}
	{
		let t = e.cacheKey;
		return t ? Zf[t] || (Zf[t] = Kf(e)) : Kf(e);
	}
}
var ep = {
	INVALID_ARGUMENT: 17,
	INVALID_DATE_ARGUMENT: 18,
	INVALID_ISO_DATE_ARGUMENT: 19,
	NOT_SUPPORT_NON_STRING_MESSAGE: 20,
	NOT_SUPPORT_LOCALE_PROMISE_VALUE: 21,
	NOT_SUPPORT_LOCALE_ASYNC_FUNCTION: 22,
	NOT_SUPPORT_LOCALE_TYPE: 23
};
function tp(e) {
	return Wd(e, null, void 0);
}
ep.INVALID_ARGUMENT, ep.INVALID_DATE_ARGUMENT, ep.INVALID_ISO_DATE_ARGUMENT, ep.NOT_SUPPORT_NON_STRING_MESSAGE, ep.NOT_SUPPORT_LOCALE_PROMISE_VALUE, ep.NOT_SUPPORT_LOCALE_ASYNC_FUNCTION, ep.NOT_SUPPORT_LOCALE_TYPE;
function np(e, t) {
	return t.locale == null ? ip(e.locale) : ip(t.locale);
}
var rp;
function ip(e) {
	if (Y(e)) return e;
	if (J(e)) {
		if (e.resolvedOnce && rp != null) return rp;
		if (e.constructor.name === "Function") {
			let t = e();
			if (Pd(t)) throw tp(ep.NOT_SUPPORT_LOCALE_PROMISE_VALUE);
			return rp = t;
		}
		throw tp(ep.NOT_SUPPORT_LOCALE_ASYNC_FUNCTION);
	}
	throw tp(ep.NOT_SUPPORT_LOCALE_TYPE);
}
function ap(e, t, n) {
	return [.../* @__PURE__ */ new Set([n, ...q(t) ? t : Z(t) ? Object.keys(t) : Y(t) ? [t] : [n]])];
}
function op(e, t, n) {
	let r = Y(n) ? n : Sp, i = e;
	i.__localeChainCache || (i.__localeChainCache = /* @__PURE__ */ new Map());
	let a = i.__localeChainCache.get(r);
	if (!a) {
		a = [];
		let e = [n];
		for (; q(e);) e = sp(a, e, t);
		let o = q(t) || !Q(t) ? t : t.default ? t.default : null;
		e = Y(o) ? [o] : o, q(e) && sp(a, e, !1), i.__localeChainCache.set(r, a);
	}
	return a;
}
function sp(e, t, n) {
	let r = !0;
	for (let i = 0; i < t.length && X(r); i++) {
		let a = t[i];
		Y(a) && (r = cp(e, t[i], n));
	}
	return r;
}
function cp(e, t, n) {
	let r, i = t.split("-");
	do
		r = lp(e, i.join("-"), n), i.splice(-1, 1);
	while (i.length && r === !0);
	return r;
}
function lp(e, t, n) {
	let r = !1;
	if (!e.includes(t) && (r = !0, t)) {
		r = t[t.length - 1] !== "!";
		let i = t.replace(/!/g, "");
		e.push(i), (q(n) || Q(n)) && n[i] && (r = n[i]);
	}
	return r;
}
var up = [];
up[0] = {
	w: [0],
	i: [3, 0],
	"[": [4],
	o: [7]
}, up[1] = {
	w: [1],
	".": [2],
	"[": [4],
	o: [7]
}, up[2] = {
	w: [2],
	i: [3, 0],
	0: [3, 0]
}, up[3] = {
	i: [3, 0],
	0: [3, 0],
	w: [1, 1],
	".": [2, 1],
	"[": [4, 1],
	o: [7, 1]
}, up[4] = {
	"'": [5, 0],
	"\"": [6, 0],
	"[": [4, 2],
	"]": [1, 3],
	o: 8,
	l: [4, 0]
}, up[5] = {
	"'": [4, 0],
	o: 8,
	l: [5, 0]
}, up[6] = {
	"\"": [4, 0],
	o: 8,
	l: [6, 0]
};
var dp = /^\s?(?:true|false|-?[\d.]+|'[^']*'|"[^"]*")\s?$/;
function fp(e) {
	return dp.test(e);
}
function pp(e) {
	let t = e.charCodeAt(0);
	return t === e.charCodeAt(e.length - 1) && (t === 34 || t === 39) ? e.slice(1, -1) : e;
}
function mp(e) {
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
function hp(e) {
	let t = e.trim();
	return e.charAt(0) === "0" && isNaN(parseInt(e)) ? !1 : fp(t) ? pp(t) : "*" + t;
}
function gp(e) {
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
			if (i = 0, o === void 0 || (o = hp(o), o === !1)) return !1;
			f[1]();
		}
	};
	function p() {
		let t = e[n + 1];
		if (r === 5 && t === "'" || r === 6 && t === "\"") return n++, s = "\\" + t, f[0](), !0;
	}
	for (; r !== null;) if (n++, a = e[n], !(a === "\\" && p())) {
		if (c = mp(a), d = up[r], l = d[c] || d.l || 8, l === 8 || (r = l[0], l[1] !== void 0 && (u = f[l[1]], u && (s = a, u() === !1)))) return;
		if (r === 7) return t;
	}
}
var _p = /* @__PURE__ */ new Map();
function vp(e, t) {
	return Z(e) ? e[t] : null;
}
function yp(e, t) {
	if (!Z(e)) return null;
	let n = _p.get(t);
	if (n || (n = gp(t), n && _p.set(t, n)), !n) return null;
	let r = n.length, i = e, a = 0;
	for (; a < r;) {
		let e = n[a];
		if (Wf.includes(e) && Ef(i) || !Z(i) || !Nd(i, e)) return null;
		let t = i[e];
		if (t === void 0 || J(i)) return null;
		i = t, a++;
	}
	return i;
}
var bp = {
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
bp.NOT_FOUND_KEY, bp.FALLBACK_TO_TRANSLATE, bp.CANNOT_FORMAT_NUMBER, bp.FALLBACK_TO_NUMBER_FORMAT, bp.CANNOT_FORMAT_DATE, bp.FALLBACK_TO_DATE_FORMAT, bp.EXPERIMENTAL_CUSTOM_MESSAGE_COMPILER, bp.INVALID_NUMBER_ARGUMENT, bp.INVALID_DATE_ARGUMENT;
var xp = "11.3.2", Sp = "en-US", Cp = (e) => `${e.charAt(0).toLocaleUpperCase()}${e.substr(1)}`;
function wp() {
	return {
		upper: (e, t) => t === "text" && Y(e) ? e.toUpperCase() : t === "vnode" && Z(e) && "__v_isVNode" in e ? e.children.toUpperCase() : e,
		lower: (e, t) => t === "text" && Y(e) ? e.toLowerCase() : t === "vnode" && Z(e) && "__v_isVNode" in e ? e.children.toLowerCase() : e,
		capitalize: (e, t) => t === "text" && Y(e) ? Cp(e) : t === "vnode" && Z(e) && "__v_isVNode" in e ? Cp(e.children) : e
	};
}
var Tp;
function Ep(e) {
	Tp = e;
}
var Dp;
function Op(e) {
	Dp = e;
}
var kp;
function Ap(e) {
	kp = e;
}
var jp = null, Mp = (e) => {
	jp = e;
}, Np = () => jp, Pp = 0;
function Fp(e = {}) {
	let t = J(e.onWarn) ? e.onWarn : _d, n = Y(e.version) ? e.version : xp, r = Y(e.locale) || J(e.locale) ? e.locale : Sp, i = J(r) ? Sp : r, a = q(e.fallbackLocale) || Q(e.fallbackLocale) || Y(e.fallbackLocale) || e.fallbackLocale === !1 ? e.fallbackLocale : i, o = Q(e.messages) ? e.messages : Ip(i), s = Q(e.datetimeFormats) ? e.datetimeFormats : Ip(i), c = Q(e.numberFormats) ? e.numberFormats : Ip(i), l = Td(K(), e.modifiers, wp()), u = e.pluralRules || K(), d = J(e.missing) ? e.missing : null, f = X(e.missingWarn) || Cd(e.missingWarn) ? e.missingWarn : !0, p = X(e.fallbackWarn) || Cd(e.fallbackWarn) ? e.fallbackWarn : !0, m = !!e.fallbackFormat, h = !!e.unresolving, g = J(e.postTranslation) ? e.postTranslation : null, _ = Q(e.processor) ? e.processor : null, v = !X(e.warnHtmlMessage) || e.warnHtmlMessage, y = !!e.escapeParameter, b = J(e.messageCompiler) ? e.messageCompiler : Tp, x = J(e.messageResolver) ? e.messageResolver : Dp || vp, S = J(e.localeFallbacker) ? e.localeFallbacker : kp || ap, C = Z(e.fallbackContext) ? e.fallbackContext : void 0, w = e, T = Z(w.__datetimeFormatters) ? w.__datetimeFormatters : /* @__PURE__ */ new Map(), E = Z(w.__numberFormatters) ? w.__numberFormatters : /* @__PURE__ */ new Map(), D = Z(w.__meta) ? w.__meta : {};
	Pp++;
	let O = {
		version: n,
		cid: Pp,
		locale: r,
		fallbackLocale: a,
		messages: o,
		modifiers: l,
		pluralRules: u,
		missing: d,
		missingWarn: f,
		fallbackWarn: p,
		fallbackFormat: m,
		unresolving: h,
		postTranslation: g,
		processor: _,
		warnHtmlMessage: v,
		escapeParameter: y,
		messageCompiler: b,
		messageResolver: x,
		localeFallbacker: S,
		fallbackContext: C,
		onWarn: t,
		__meta: D
	};
	return O.datetimeFormats = s, O.numberFormats = c, O.__datetimeFormatters = T, O.__numberFormatters = E, O;
}
var Ip = (e) => ({ [e]: K() });
function Lp(e, t, n, r, i) {
	let { missing: a, onWarn: o } = e;
	if (a !== null) {
		let r = a(e, n, t, i);
		return Y(r) ? r : t;
	}
	return t;
}
function Rp(e, t, n) {
	let r = e;
	r.__localeChainCache = /* @__PURE__ */ new Map(), e.localeFallbacker(e, n, t);
}
function zp(e, t) {
	return e !== t && e.split("-")[0] === t.split("-")[0];
}
function Bp(e, t) {
	let n = t.indexOf(e);
	if (n === -1) return !1;
	for (let r = n + 1; r < t.length; r++) if (zp(e, t[r])) return !0;
	return !1;
}
var Vp = typeof Intl < "u";
Vp && Intl.DateTimeFormat, Vp && Intl.NumberFormat;
function Hp(e, ...t) {
	let { datetimeFormats: n, unresolving: r, fallbackLocale: i, onWarn: a, localeFallbacker: o } = e, { __datetimeFormatters: s } = e;
	if (!Y(t[0]) && !Sd(t[0]) && !G(t[0])) return "";
	let [c, l, u, d] = Wp(...t), f = X(u.missingWarn) ? u.missingWarn : e.missingWarn;
	X(u.fallbackWarn) ? u.fallbackWarn : e.fallbackWarn;
	let p = !!u.part, m = np(e, u), h = o(e, i, m);
	if (!Y(c) || c === "") return new Intl.DateTimeFormat(m.replace(/!/g, ""), d).format(l);
	let g = {}, _, v = null;
	for (let t = 0; t < h.length && (_ = h[t], g = n[_] || {}, v = g[c], !Q(v)); t++) Lp(e, c, _, f, "datetime format");
	if (!Q(v) || !Y(_)) return r ? -1 : c;
	let y = `${_}__${c}`;
	wd(d) || (y = `${y}__${JSON.stringify(d)}`);
	let b = s.get(y);
	return b || (b = new Intl.DateTimeFormat(_, Td({}, v, d)), s.set(y, b)), p ? b.formatToParts(l) : b.format(l);
}
var Up = [
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
function Wp(...e) {
	let [t, n, r, i] = e, a = K(), o = K(), s;
	if (Y(t)) {
		let e = t.match(/(\d{4}-\d{2}-\d{2})(T|\s)?(.*)/);
		if (!e) throw tp(ep.INVALID_ISO_DATE_ARGUMENT);
		let n = e[3] ? e[3].trim().startsWith("T") ? `${e[1].trim()}${e[3].trim()}` : `${e[1].trim()}T${e[3].trim()}` : e[1].trim();
		s = new Date(n);
		try {
			s.toISOString();
		} catch {
			throw tp(ep.INVALID_ISO_DATE_ARGUMENT);
		}
	} else if (Sd(t)) {
		if (isNaN(t.getTime())) throw tp(ep.INVALID_DATE_ARGUMENT);
		s = t;
	} else if (G(t)) s = t;
	else throw tp(ep.INVALID_ARGUMENT);
	return Y(n) ? a.key = n : Q(n) && Object.keys(n).forEach((e) => {
		Up.includes(e) ? o[e] = n[e] : a[e] = n[e];
	}), Y(r) ? a.locale = r : Q(r) && (o = r), Q(i) && (o = i), [
		a.key || "",
		s,
		a,
		o
	];
}
function Gp(e, t, n) {
	let r = e;
	for (let e in n) {
		let n = `${t}__${e}`;
		r.__datetimeFormatters.has(n) && r.__datetimeFormatters.delete(n);
	}
}
function Kp(e, ...t) {
	let { numberFormats: n, unresolving: r, fallbackLocale: i, onWarn: a, localeFallbacker: o } = e, { __numberFormatters: s } = e;
	if (!G(t[0])) return "";
	let [c, l, u, d] = Jp(...t), f = X(u.missingWarn) ? u.missingWarn : e.missingWarn;
	X(u.fallbackWarn) ? u.fallbackWarn : e.fallbackWarn;
	let p = !!u.part, m = np(e, u), h = o(e, i, m);
	if (!Y(c) || c === "") return new Intl.NumberFormat(m.replace(/!/g, ""), d).format(l);
	let g = {}, _, v = null;
	for (let t = 0; t < h.length && (_ = h[t], g = n[_] || {}, v = g[c], !Q(v)); t++) Lp(e, c, _, f, "number format");
	if (!Q(v) || !Y(_)) return r ? -1 : c;
	let y = `${_}__${c}`;
	wd(d) || (y = `${y}__${JSON.stringify(d)}`);
	let b = s.get(y);
	return b || (b = new Intl.NumberFormat(_, Td({}, v, d)), s.set(y, b)), p ? b.formatToParts(l) : b.format(l);
}
var qp = [
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
function Jp(...e) {
	let [t, n, r, i] = e, a = K(), o = K();
	if (!G(t)) throw tp(ep.INVALID_ARGUMENT);
	let s = t;
	return Y(n) ? a.key = n : Q(n) && Object.keys(n).forEach((e) => {
		qp.includes(e) ? o[e] = n[e] : a[e] = n[e];
	}), Y(r) ? a.locale = r : Q(r) && (o = r), Q(i) && (o = i), [
		a.key || "",
		s,
		a,
		o
	];
}
function Yp(e, t, n) {
	let r = e;
	for (let e in n) {
		let n = `${t}__${e}`;
		r.__numberFormatters.has(n) && r.__numberFormatters.delete(n);
	}
}
var Xp = (e) => e, Zp = (e) => "", Qp = "text", $p = (e) => e.length === 0 ? "" : Rd(e), em = Ld;
function tm(e, t) {
	return e = Math.abs(e), t === 2 ? e === 1 ? 0 : 1 : Math.min(e, 2);
}
function nm(e) {
	let t = G(e.pluralIndex) ? e.pluralIndex : -1;
	return G(e.named?.count) ? e.named.count : G(e.named?.n) ? e.named.n : t;
}
function rm(e = {}) {
	let t = e.locale, n = nm(e), r = Y(t) && J(e.pluralRules?.[t]) ? e.pluralRules[t] : tm, i = r === tm ? void 0 : tm, a = (e) => e[r(n, e.length, i)], o = e.list || [], s = (e) => o[e], c = e.named || K();
	G(e.pluralIndex) && (c.count || (c.count = e.pluralIndex), c.n || (c.n = e.pluralIndex));
	let l = (e) => c[e];
	function u(t, n) {
		return (J(e.messages) ? e.messages(t, !!n) : Z(e.messages) ? e.messages[t] : !1) || (e.parent ? e.parent.message(t) : Zp);
	}
	let d = (t) => e.modifiers ? e.modifiers[t] : Xp, f = J(e.processor?.normalize) ? e.processor.normalize : $p, p = J(e.processor?.interpolate) ? e.processor.interpolate : em, m = {
		list: s,
		named: l,
		plural: a,
		linked: (e, ...t) => {
			let [n, r] = t, i = "text", a = "";
			t.length === 1 ? Z(n) ? (a = n.modifier || a, i = n.type || i) : Y(n) && (a = n || a) : t.length === 2 && (Y(n) && (a = n || a), Y(r) && (i = r || i));
			let o = u(e, !0)(m), s = o === "" || o === void 0 ? e : o, c = i === "vnode" && q(s) && a ? s[0] : s;
			return a ? d(a)(c, i) : c;
		},
		message: u,
		type: Y(e.processor?.type) ? e.processor.type : Qp,
		interpolate: p,
		normalize: f,
		values: Td(K(), o, c)
	};
	return m;
}
var im = () => "", am = (e) => J(e);
function om(e, ...t) {
	let { fallbackFormat: n, postTranslation: r, unresolving: i, messageCompiler: a, fallbackLocale: o, messages: s } = e, [c, l] = dm(...t), u = X(l.missingWarn) ? l.missingWarn : e.missingWarn, d = X(l.fallbackWarn) ? l.fallbackWarn : e.fallbackWarn, f = X(l.escapeParameter) ? l.escapeParameter : e.escapeParameter, p = !!l.resolvedMessage, m = Y(l.default) || X(l.default) ? X(l.default) ? a ? c : () => c : l.default : n ? a ? c : () => c : null, h = n || m != null && (Y(m) || J(m)), g = np(e, l);
	f && sm(l);
	let [_, v, y] = p ? [
		c,
		g,
		s[g] || K()
	] : cm(e, c, g, o, d, u), b = _, x = c;
	if (!p && !(Y(b) || Ef(b) || am(b)) && h && (b = m, x = b), !p && (!(Y(b) || Ef(b) || am(b)) || !Y(v))) return i ? -1 : c;
	let S = !1, C = am(b) ? b : lm(e, c, v, b, x, () => {
		S = !0;
	});
	if (S) return b;
	let w = um(e, C, rm(pm(e, v, y, l))), T = r ? r(w, c) : w;
	return f && Y(T) && (T = jd(T)), T;
}
function sm(e) {
	q(e.list) ? e.list = e.list.map((e) => Y(e) ? kd(e) : e) : Z(e.named) && Object.keys(e.named).forEach((t) => {
		Y(e.named[t]) && (e.named[t] = kd(e.named[t]));
	});
}
function cm(e, t, n, r, i, a) {
	let { messages: o, onWarn: s, messageResolver: c, localeFallbacker: l } = e, u = l(e, r, n), d = K(), f, p = null;
	for (let n = 0; n < u.length && (f = u[n], d = o[f] || K(), (p = c(d, t)) === null && (p = d[t]), !(Y(p) || Ef(p) || am(p))); n++) if (!Bp(f, u)) {
		let n = Lp(e, t, f, a, "translate");
		n !== t && (p = n);
	}
	return [
		p,
		f,
		d
	];
}
function lm(e, t, n, r, i, a) {
	let { messageCompiler: o, warnHtmlMessage: s } = e;
	if (am(r)) {
		let e = r;
		return e.locale = e.locale || n, e.key = e.key || t, e;
	}
	if (o == null) {
		let e = (() => r);
		return e.locale = n, e.key = t, e;
	}
	let c = o(r, fm(e, n, i, r, s, a));
	return c.locale = n, c.key = t, c.source = r, c;
}
function um(e, t, n) {
	return t(n);
}
function dm(...e) {
	let [t, n, r] = e, i = K();
	if (!Y(t) && !G(t) && !am(t) && !Ef(t)) throw tp(ep.INVALID_ARGUMENT);
	let a = G(t) ? String(t) : (am(t), t);
	return G(n) ? i.plural = n : Y(n) ? i.default = n : Q(n) && !wd(n) ? i.named = n : q(n) && (i.list = n), G(r) ? i.plural = r : Y(r) ? i.default = r : Q(r) && Td(i, r), [a, i];
}
function fm(e, t, n, r, i, a) {
	return {
		locale: t,
		key: n,
		warnHtmlMessage: i,
		onError: (e) => {
			throw a && a(e), e;
		},
		onCacheKey: (e) => bd(t, n, e)
	};
}
function pm(e, t, n, r) {
	let { modifiers: i, pluralRules: a, messageResolver: o, fallbackLocale: s, fallbackWarn: c, missingWarn: l, fallbackContext: u } = e, d = {
		locale: t,
		modifiers: i,
		pluralRules: a,
		messages: (r, i) => {
			let a = o(n, r);
			if (a == null && (u || i)) {
				let [n, , i] = cm(u || e, r, t, s, c, l);
				a = n ?? o(i, r);
			}
			if (Y(a) || Ef(a)) {
				let n = !1, i = lm(e, r, t, a, r, () => {
					n = !0;
				});
				return n ? im : i;
			}
			return am(a) ? a : im;
		}
	};
	return e.processor && (d.processor = e.processor), r.list && (d.list = r.list), r.named && (d.named = r.named), G(r.plural) && (d.pluralIndex = r.plural), d;
}
Tf();
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
	typeof __INTLIFY_DROP_MESSAGE_COMPILER__ != "boolean" && (Od().__INTLIFY_DROP_MESSAGE_COMPILER__ = !1);
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
	return Wd(e, null, void 0);
}
Dm.UNEXPECTED_RETURN_TYPE, Dm.INVALID_ARGUMENT, Dm.MUST_BE_CALL_SETUP_TOP, Dm.NOT_INSTALLED, Dm.UNEXPECTED_ERROR, Dm.REQUIRED_VALUE, Dm.INVALID_VALUE, Dm.CANNOT_SETUP_VUE_DEVTOOLS_PLUGIN, Dm.NOT_INSTALLED_WITH_PROVIDE, Dm.NOT_COMPATIBLE_LEGACY_VUE_I18N, Dm.NOT_AVAILABLE_COMPOSITION_IN_LEGACY;
var km = /* #__PURE__*/ yd("__translateVNode"), Am = /* #__PURE__*/ yd("__datetimeParts"), jm = /* #__PURE__*/ yd("__numberParts"), Mm = /* #__PURE__*/ yd("__enableEmitter"), Nm = yd("__setPluralRules");
yd("__intlifyMeta");
var Pm = /* #__PURE__*/ yd("__injectWithOption"), Fm = {
	FALLBACK_TO_ROOT: 10,
	NOT_FOUND_PARENT_SCOPE: 11,
	IGNORE_OBJ_FLATTEN: 12,
	DEPRECATE_LEGACY_MODE: 13,
	DEPRECATE_TRANSLATE_CUSTOME_DIRECTIVE: 14,
	DUPLICATE_USE_I18N_CALLING: 15
};
Fm.FALLBACK_TO_ROOT, Fm.NOT_FOUND_PARENT_SCOPE, Fm.IGNORE_OBJ_FLATTEN, Fm.DEPRECATE_LEGACY_MODE, Fm.DEPRECATE_TRANSLATE_CUSTOME_DIRECTIVE, Fm.DUPLICATE_USE_I18N_CALLING;
function Im(e) {
	if (!Z(e) || Ef(e)) return e;
	for (let t in e) if (Nd(e, t)) {
		if (!t.includes(".")) Z(e[t]) && Im(e[t]);
		else {
			let n = t.split("."), r = n.length - 1, i = e, a = !1;
			for (let e = 0; e < r; e++) {
				if (n[e] === "__proto__") throw Error(`unsafe key: ${n[e]}`);
				if (n[e] in i || (i[n[e]] = K()), !Z(i[n[e]])) {
					a = !0;
					break;
				}
				i = i[n[e]];
			}
			if (a || (Ef(i) ? Wf.includes(n[r]) || delete e[t] : (i[n[r]] = e[t], delete e[t])), !Ef(i)) {
				let e = i[n[r]];
				Z(e) && Im(e);
			}
		}
	}
	return e;
}
function Lm(e, t) {
	let { messages: n, __i18n: r, messageResolver: i, flatJson: a } = t, o = Q(n) ? n : q(r) ? K() : { [e]: K() };
	if (q(r) && r.forEach((e) => {
		if ("locale" in e && "resource" in e) {
			let { locale: t, resource: n } = e;
			t ? (o[t] = o[t] || K(), Vd(n, o[t])) : Vd(n, o);
		} else Y(e) && Vd(JSON.parse(e), o);
	}), i == null && a) for (let e in o) Nd(o, e) && Im(o[e]);
	return o;
}
function Rm(e) {
	return W(Ks, null, e, 0);
}
function zm() {
	let e = "currentInstance";
	return e in hd ? hd[e] : wc();
}
var Bm = () => [], Vm = () => !1, Hm = 0;
function Um(e) {
	return ((t, n, r, i) => e(n, r, zm() || void 0, i));
}
function Wm(e = {}) {
	let { __root: t, __injectWithOption: n } = e, r = t === void 0, i = e.flatJson, a = vd ? xn : Sn, o = !X(e.inheritLocale) || e.inheritLocale, s = a(t && o ? t.locale.value : Y(e.locale) ? e.locale : Sp), c = a(t && o ? t.fallbackLocale.value : Y(e.fallbackLocale) || q(e.fallbackLocale) || Q(e.fallbackLocale) || e.fallbackLocale === !1 ? e.fallbackLocale : s.value), l = a(Lm(s.value, e)), u = a(Q(e.datetimeFormats) ? e.datetimeFormats : { [s.value]: {} }), d = a(Q(e.numberFormats) ? e.numberFormats : { [s.value]: {} }), f = t ? t.missingWarn : X(e.missingWarn) || Cd(e.missingWarn) ? e.missingWarn : !0, p = t ? t.fallbackWarn : X(e.fallbackWarn) || Cd(e.fallbackWarn) ? e.fallbackWarn : !0, m = t ? t.fallbackRoot : !X(e.fallbackRoot) || e.fallbackRoot, h = !!e.fallbackFormat, g = J(e.missing) ? e.missing : null, _ = J(e.missing) ? Um(e.missing) : null, v = J(e.postTranslation) ? e.postTranslation : null, y = t ? t.warnHtmlMessage : !X(e.warnHtmlMessage) || e.warnHtmlMessage, b = !!e.escapeParameter, x = t ? t.modifiers : Q(e.modifiers) ? e.modifiers : {}, S = e.pluralRules || t && t.pluralRules, C;
	C = (() => {
		r && Mp(null);
		let t = {
			version: Tm,
			locale: s.value,
			fallbackLocale: c.value,
			messages: l.value,
			modifiers: x,
			pluralRules: S,
			missing: _ === null ? void 0 : _,
			missingWarn: f,
			fallbackWarn: p,
			fallbackFormat: h,
			unresolving: !0,
			postTranslation: v === null ? void 0 : v,
			warnHtmlMessage: y,
			escapeParameter: b,
			messageResolver: e.messageResolver,
			messageCompiler: e.messageCompiler,
			__meta: { framework: "vue" }
		};
		t.datetimeFormats = u.value, t.numberFormats = d.value, t.__datetimeFormatters = Q(C) ? C.__datetimeFormatters : void 0, t.__numberFormatters = Q(C) ? C.__numberFormatters : void 0;
		let n = Fp(t);
		return r && Mp(n), n;
	})(), Rp(C, s.value, c.value);
	function w() {
		return [
			s.value,
			c.value,
			l.value,
			u.value,
			d.value
		];
	}
	let T = qc({
		get: () => s.value,
		set: (e) => {
			C.locale = e, s.value = e;
		}
	}), E = qc({
		get: () => c.value,
		set: (e) => {
			C.fallbackLocale = e, c.value = e, Rp(C, s.value, e);
		}
	}), D = qc(() => l.value), O = /* #__PURE__*/ qc(() => u.value), k = /* #__PURE__*/ qc(() => d.value);
	function A() {
		return J(v) ? v : null;
	}
	function ee(e) {
		v = e, C.postTranslation = e;
	}
	function te() {
		return g;
	}
	function ne(e) {
		e !== null && (_ = Um(e)), g = e, C.missing = _;
	}
	let re = (e, n, i, a, o, s) => {
		w();
		let c;
		try {
			r || (C.fallbackContext = t ? Np() : void 0), c = e(C);
		} finally {
			r || (C.fallbackContext = void 0);
		}
		if (i !== "translate exists" && G(c) && c === -1 || i === "translate exists" && !c) {
			let [e, r] = n();
			return t && m ? a(t) : o(e);
		}
		if (s(c)) return c;
		/* istanbul ignore next */
		throw Om(Dm.UNEXPECTED_RETURN_TYPE);
	};
	function j(...e) {
		return re((t) => Reflect.apply(om, null, [t, ...e]), () => dm(...e), "translate", (t) => Reflect.apply(t.t, t, [...e]), (e) => e, (e) => Y(e));
	}
	function ie(...e) {
		let [t, n, r] = e;
		if (r && !Z(r)) throw Om(Dm.INVALID_ARGUMENT);
		return j(t, n, Td({ resolvedMessage: !0 }, r || {}));
	}
	function ae(...e) {
		return re((t) => Reflect.apply(Hp, null, [t, ...e]), () => Wp(...e), "datetime format", (t) => Reflect.apply(t.d, t, [...e]), () => "", (e) => Y(e) || q(e));
	}
	function oe(...e) {
		return re((t) => Reflect.apply(Kp, null, [t, ...e]), () => Jp(...e), "number format", (t) => Reflect.apply(t.n, t, [...e]), () => "", (e) => Y(e) || q(e));
	}
	function se(e) {
		return e.map((e) => Y(e) || G(e) || X(e) ? Rm(String(e)) : e);
	}
	let M = {
		normalize: se,
		interpolate: (e) => e,
		type: "vnode"
	};
	function ce(...e) {
		return re((t) => {
			let n, r = t;
			try {
				r.processor = M, n = Reflect.apply(om, null, [r, ...e]);
			} finally {
				r.processor = null;
			}
			return n;
		}, () => dm(...e), "translate", (t) => t[km](...e), (e) => [Rm(e)], (e) => q(e));
	}
	function N(...e) {
		return re((t) => Reflect.apply(Kp, null, [t, ...e]), () => Jp(...e), "number format", (t) => t[jm](...e), Bm, (e) => Y(e) || q(e));
	}
	function le(...e) {
		return re((t) => Reflect.apply(Hp, null, [t, ...e]), () => Wp(...e), "datetime format", (t) => t[Am](...e), Bm, (e) => Y(e) || q(e));
	}
	function ue(e) {
		S = e, C.pluralRules = S;
	}
	function P(e, t) {
		return re(() => {
			if (!e) return !1;
			let n = Y(t) ? t : s.value, r = Y(t) ? [n] : op(C, c.value, n);
			for (let t = 0; t < r.length; t++) {
				let n = pe(r[t]), i = C.messageResolver(n, e);
				if (i === null && (i = n[e]), Ef(i) || am(i) || Y(i)) return !0;
			}
			return !1;
		}, () => [e], "translate exists", (n) => Reflect.apply(n.te, n, [e, t]), Vm, (e) => X(e));
	}
	function de(e) {
		let t = null, n = op(C, c.value, s.value);
		for (let r = 0; r < n.length; r++) {
			let i = l.value[n[r]] || {}, a = C.messageResolver(i, e);
			if (a != null) {
				t = a;
				break;
			}
		}
		return t;
	}
	function fe(e) {
		return de(e) ?? (t && t.tm(e) || {});
	}
	function pe(e) {
		return l.value[e] || {};
	}
	function me(e, t) {
		if (i) {
			let n = { [e]: t };
			for (let e in n) Nd(n, e) && Im(n[e]);
			t = n[e];
		}
		l.value[e] = t, C.messages = l.value;
	}
	function he(e, t) {
		l.value[e] = l.value[e] || {};
		let n = { [e]: t };
		if (i) for (let e in n) Nd(n, e) && Im(n[e]);
		t = n[e], Vd(t, l.value[e]), C.messages = l.value;
	}
	function ge(e) {
		return u.value[e] || {};
	}
	function _e(e, t) {
		u.value[e] = t, C.datetimeFormats = u.value, Gp(C, e, t);
	}
	function ve(e, t) {
		u.value[e] = Td(u.value[e] || {}, t), C.datetimeFormats = u.value, Gp(C, e, t);
	}
	function ye(e) {
		return d.value[e] || {};
	}
	function be(e, t) {
		d.value[e] = t, C.numberFormats = d.value, Yp(C, e, t);
	}
	function xe(e, t) {
		d.value[e] = Td(d.value[e] || {}, t), C.numberFormats = d.value, Yp(C, e, t);
	}
	Hm++, t && vd && (ni(t.locale, (e) => {
		o && (s.value = e, C.locale = e, Rp(C, s.value, c.value));
	}), ni(t.fallbackLocale, (e) => {
		o && (c.value = e, C.fallbackLocale = e, Rp(C, s.value, c.value));
	}));
	let F = {
		id: Hm,
		locale: T,
		fallbackLocale: E,
		get inheritLocale() {
			return o;
		},
		set inheritLocale(e) {
			o = e, e && t && (s.value = t.locale.value, c.value = t.fallbackLocale.value, Rp(C, s.value, c.value));
		},
		get availableLocales() {
			return Object.keys(l.value).sort();
		},
		messages: D,
		get modifiers() {
			return x;
		},
		get pluralRules() {
			return S || {};
		},
		get isGlobal() {
			return r;
		},
		get missingWarn() {
			return f;
		},
		set missingWarn(e) {
			f = e, C.missingWarn = f;
		},
		get fallbackWarn() {
			return p;
		},
		set fallbackWarn(e) {
			p = e, C.fallbackWarn = p;
		},
		get fallbackRoot() {
			return m;
		},
		set fallbackRoot(e) {
			m = e;
		},
		get fallbackFormat() {
			return h;
		},
		set fallbackFormat(e) {
			h = e, C.fallbackFormat = h;
		},
		get warnHtmlMessage() {
			return y;
		},
		set warnHtmlMessage(e) {
			y = e, C.warnHtmlMessage = e;
		},
		get escapeParameter() {
			return b;
		},
		set escapeParameter(e) {
			b = e, C.escapeParameter = e;
		},
		t: j,
		getLocaleMessage: pe,
		setLocaleMessage: me,
		mergeLocaleMessage: he,
		getPostTranslationHandler: A,
		setPostTranslationHandler: ee,
		getMissingHandler: te,
		setMissingHandler: ne,
		[Nm]: ue
	};
	return F.datetimeFormats = O, F.numberFormats = k, F.rt = ie, F.te = P, F.tm = fe, F.d = ae, F.n = oe, F.getDateTimeFormat = ge, F.setDateTimeFormat = _e, F.mergeDateTimeFormat = ve, F.getNumberFormat = ye, F.setNumberFormat = be, F.mergeNumberFormat = xe, F[Pm] = n, F[km] = ce, F[Am] = le, F[jm] = N, F;
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
		J(r) && "source" in r ? t[n] = ih(r) : Ef(r) && r.loc && r.loc.source ? t[n] = r.loc.source : Z(r) ? t[n] = eh(r) : t[n] = r;
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
		t === "locale" && Y(e.state.value) ? n.locale.value = e.state.value : t === "fallbackLocale" && (Y(e.state.value) || q(e.state.value) || Z(e.state.value)) ? n.fallbackLocale.value = e.state.value : t === "inheritLocale" && X(e.state.value) && (n.inheritLocale = e.state.value);
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
Td({
	keypath: {
		type: String,
		required: !0
	},
	plural: {
		type: [Number, String],
		validator: (e) => G(e) || !isNaN(e)
	}
}, fh), Td({
	value: {
		type: Number,
		required: !0
	},
	format: { type: [String, Object] }
}, fh);
function ph(e = {}) {
	let t = !X(e.globalInjection) || e.globalInjection, n = /* @__PURE__ */ new Map(), [r, i] = mh(e, !1), a = /* #__PURE__*/ yd("");
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
			if (__VUE_PROD_DEVTOOLS__ && (e.__VUE_I18N__ = l), e.__VUE_I18N_SYMBOL__ = a, e.provide(e.__VUE_I18N_SYMBOL__, l), Q(n[0])) {
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
				let t = zd();
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
	let n = Ke(), r = n.run(() => Wm(e));
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
		let i = /* @__PURE__ */ z(r.value) ? {
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
Td({
	value: {
		type: [Number, Date],
		required: !0
	},
	format: { type: [String, Object] }
}, fh), Em(), Ep($f), Op(yp), Ap(op);
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
	"navigation.android.forceRefresh": "Force refresh (keep login)",
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
		"navigation.android.forceRefresh": "Neu laden erzwingen (Anmeldung behalten)",
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
		"navigation.android.forceRefresh": "Forzar actualización (mantener sesión iniciada)",
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
		"navigation.android.forceRefresh": "Forcer l'actualisation (conserver la connexion)",
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
		"navigation.android.forceRefresh": "Forçar atualização (manter login)",
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
function vg(e) {
	try {
		e ?? (e = globalThis.localStorage);
		let t = JSON.parse(e?.getItem("twicc-settings") || "{}")?.locale;
		return _g(t) ? t : "en";
	} catch {
		return "en";
	}
}
var yg = {
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
}, bg = { fr: (e, t) => t === 2 ? Math.abs(e) < 2 ? 0 : 1 : e ? Math.abs(e) === 1 ? 1 : 2 : 0 };
function xg(e = yg) {
	let t = /* @__PURE__ */ new Map([["en", vh]]), n = /* @__PURE__ */ new Map(), r = 0;
	function i(e = "en") {
		return ph({
			legacy: !1,
			globalInjection: !0,
			locale: _g(e) ? e : "en",
			fallbackLocale: "en",
			pluralRules: bg,
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
var { createAppI18n: Sg, i18n: Cg, loadLocaleMessages: wg, setLocale: Tg } = xg(), Eg = {
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
	nativeSources: /* @__PURE__ */ JSON.parse("{\"android/app/src/main/res/values/strings.xml\":{\"Timeline speech\":\"android.speech.channel\",\"%1$s · Speech\":\"android.speech.notificationTitle\",\"Preparing\":\"android.speech.preparing\",\"Reading\":\"android.speech.reading\",\"Waiting\":\"android.speech.waiting\",\"Reconnecting\":\"android.speech.reconnecting\",\"Stopping\":\"android.speech.stopping\",\"Skip\":\"android.speech.skip\",\"Stop all\":\"android.speech.stopAll\",\"Pause timeline\":\"android.speech.pauseTimeline\",\"Resume timeline\":\"android.speech.resumeTimeline\",\"Timeline reading paused\":\"android.speech.timelinePaused\",\"Enable speech notifications to use background Skip and Stop all controls. Then start speech again.\":\"android.speech.notificationsRequired\",\"Timeline listening and battery\":\"android.speech.batteryTitle\",\"You can use timeline listening without changing battery settings. Android may delay or interrupt speech in the background or with the screen off. Allow unrestricted battery use for TwiCC to improve background listening. You can change this later in Android Settings → Apps → TwiCC → Battery.\\\\n\\\\nContinue without changes to start now, or open Settings and then enable listening again.\":\"android.speech.batteryAdvice\",\"Continue without changes\":\"android.speech.continueWithoutChanges\",\"Speech settings required\":\"android.speech.settingsTitle\",\"Open Settings\":\"android.speech.openSettings\",\"TwiCC\":\"android.instances.title\",\"Connect to your existing TwiCC server through an HTTPS tunnel.\":\"android.instances.subtitle\",\"Saved instances\":\"android.instances.saved\",\"Last used\":\"android.instances.lastUsed\",\"No instances yet\":\"android.instances.emptyTitle\",\"Add the address of your TwiCC server to get started.\":\"android.instances.emptyBody\",\"Add instance\":\"android.instances.add\",\"Edit instance\":\"android.instances.editTitle\",\"Name (optional)\":\"android.instances.nameLabel\",\"Server address\":\"android.instances.addressLabel\",\"HTTPS only. A new address starts with a fresh login.\":\"android.instances.addressHint\",\"Save\":\"common.actions.save\",\"Cancel\":\"common.actions.cancel\",\"Remove\":\"android.instances.remove\",\"Remove %1$s?\":\"android.instances.removeTitle\",\"Remove this address and request deletion of its login and Web storage in each engine. The server is unchanged.\":\"android.instances.removeMessage\",\"Open %1$s, %2$s\":\"android.instances.openDescription\",\"Edit %1$s\":\"android.instances.editDescription\",\"Remove %1$s\":\"android.instances.removeDescription\"},\"android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt\":{\"Could not complete this action. Reload this page and try again.\":\"common.android.errors.actionFailed\",\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"Android notifications are unavailable. Update the Android app and try again.\":\"android.push.errors.updateApp\",\"This action contains too much data. Try a smaller selection.\":\"common.android.errors.tooLarge\"},\"android/app/src/main/java/com/twidi/twicc/BrowserActivity.kt\":{\"Android app menu for ${instance.name}\":\"android.browser.menuDescription\",\"${EngineProvider.engine.displayName} could not start. Check the engine installation, then reopen TwiCC.\":\"android.browser.errors.engineStart\",\"This link type is not supported.\":\"android.browser.errors.linkUnsupported\",\"The page stopped unexpectedly. Retry to reopen TwiCC.\":\"android.browser.errors.rendererStopped\",\"Manage instances\":\"navigation.android.manage\",\"TwiCC ${BuildConfig.VERSION_NAME} · ${EngineProvider.engine.displayName}\":\"android.browser.version\",\"Reload page\":\"navigation.android.reload\",\"Force refresh (keep login)\":\"navigation.android.forceRefresh\",\"Force refresh?\":\"navigation.android.refreshQuestion\",\"Reload this page and bypass its resource cache. Your login and preferences stay saved. Unsaved text may be lost.\":\"navigation.android.refreshHint\",\"Server home\":\"navigation.android.serverHome\",\"Open in browser\":\"navigation.android.browser\",\"This instance is no longer saved.\":\"android.browser.errors.instanceRemoved\",\"The browser session is closed.\":\"android.browser.errors.sessionClosed\",\"Choose how to continue in the battery advice dialog.\":\"android.speech.errors.batteryChoiceRequired\",\"Speech is getting ready. Try again in a moment.\":\"common.android.errors.speechPreparing\",\"Retry\":\"common.actions.retry\",\"Cancel\":\"common.actions.cancel\",\"Refresh\":\"navigation.android.refresh\"},\"android/app/src/main/java/com/twidi/twicc/InstanceStore.kt\":{\"This server address is already saved.\":\"android.instances.errors.duplicate\"},\"android/app/src/main/java/com/twidi/twicc/MainActivity.kt\":{\"Enter a valid HTTPS address.\":\"android.instances.errors.invalidAddress\"},\"android/app/src/main/java/com/twidi/twicc/platform/BrowserDownloads.kt\":{\"This download needs a browser. Open the page in your browser to save blob or data exports.\":\"android.downloads.browserRequired\",\"Download started. Check Android's Downloads notification.\":\"android.downloads.started\",\"The download could not start. Try opening this page in your browser.\":\"android.downloads.errors.startFailed\",\"This download cannot be saved. Try opening the page in your browser.\":\"android.downloads.errors.saveUnavailable\",\"Saving $filename to Downloads…\":\"android.downloads.saving\",\"Saved $filename to Downloads.\":\"android.downloads.saved\",\"Download failed. Check your connection and try again.\":\"android.downloads.errors.failed\"},\"android/app/src/main/java/com/twidi/twicc/platform/ExternalLinks.kt\":{\"This link type is not supported.\":\"android.browser.errors.linkUnsupported\",\"Open in browser\":\"navigation.android.browser\",\"No browser is available to open this link.\":\"android.browser.errors.browserUnavailable\",\"No application can open this link.\":\"android.browser.errors.applicationUnavailable\"},\"android/app/src/main/java/com/twidi/twicc/platform/NativeRequests.kt\":{\"No file picker is available.\":\"android.files.errors.pickerUnavailable\",\"OK\":\"common.actions.ok\",\"Cancel\":\"common.actions.cancel\"},\"android/app/src/main/java/com/twidi/twicc/push/AndroidPush.kt\":{\"Finished work\":\"android.push.channels.finished\",\"Questions and approvals\":\"android.push.channels.attention\",\"Extra usage\":\"settings.notifications.targets.extraUsage\",\"Peer messages and requests\":\"android.push.channels.peer\",\"TwiCC notifications\":\"android.push.distributorLabel\"},\"android/app/src/main/java/com/twidi/twicc/push/AndroidPushService.kt\":{\"${payload.title} · ${saved.name}\":\"android.push.titleWithInstance\"},\"android/app/src/main/java/com/twidi/twicc/tts/NativeTtsBridge.kt\":{\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"Speech is getting ready. Try again in a moment.\":\"common.android.errors.speechPreparing\",\"Reload this page, then try speech again.\":\"android.speech.errors.staleDocument\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\"},\"android/app/src/main/java/com/twidi/twicc/tts/PcmExtractor.kt\":{\"Unsupported speech audio format\":\"android.speech.errors.audioFormat\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsConnection.kt\":{\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"TTS request failed ($status)\":\"android.speech.errors.audioRequest\",\"TTS audio is empty\":\"android.speech.errors.audioEmpty\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsNotification.kt\":{\"Open TwiCC before starting speech.\":\"android.speech.errors.visibleRequired\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsPlayer.kt\":{\"Speech generation failed\":\"android.speech.errors.generationFailed\",\"The audio stopped before the end\":\"session.speech.errors.cut\",\"Audio playback was interrupted.\":\"android.speech.errors.interrupted\",\"Audio playback could not start.\":\"android.speech.errors.playbackFailed\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"The audio is no longer available\":\"session.speech.errors.gone\",\"Could not reach TwiCC. Start playback again.\":\"android.speech.errors.retryPlayback\",\"Unsupported speech audio format\":\"android.speech.errors.audioFormat\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt\":{\"The active instance was closed. Start speech again to continue.\":\"android.speech.errors.instanceClosed\"},\"android/app/src/main/java/com/twidi/twicc/tts/TtsServiceOwner.kt\":{\"Speech is unavailable. Update the Android app and try again.\":\"android.speech.errors.updateRequired\",\"The active instance changed.\":\"android.speech.errors.instanceChanged\",\"Open a saved instance before starting speech.\":\"android.speech.errors.instanceRequired\",\"Reload this page, then try speech again.\":\"android.speech.errors.staleDocument\",\"Speech playback changed. Try again.\":\"android.speech.errors.playbackChanged\",\"Could not change speech playback. Reload the page and try again.\":\"android.speech.errors.changeFailed\",\"Reopen this instance, then start speech again.\":\"android.speech.errors.credentialsUnavailable\",\"Speech is stopping. Wait a moment, then try again.\":\"android.speech.errors.stopping\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"This notification belongs to an old speech session.\":\"android.speech.errors.staleSession\",\"Reopen this instance and start speech again.\":\"android.speech.errors.credentialsUnavailable\",\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"Android stopped speech. Start it again.\":\"android.speech.errors.androidStopped\"},\"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoBrowserSession.kt\":{\"The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.\":\"android.browser.errors.certificate\",\"Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.\":\"android.browser.errors.unreachable\",\"Native speech is still initializing. Retry speech.\":\"common.android.errors.speechPreparing\"},\"android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoPrompts.kt\":{\"Folder uploads are not available. Select individual files instead.\":\"android.files.errors.folderUnsupported\",\"The selected files could not be read. Try again.\":\"android.files.errors.readFailed\"},\"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewBrowserSession.kt\":{\"This page cannot open inside TwiCC. Retry to return to your server.\":\"android.browser.errors.pageBlocked\",\"Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.\":\"android.browser.errors.unreachable\",\"The server returned HTTP ${response.statusCode}. Check your tunnel or server, then retry.\":\"android.browser.errors.httpStatus\",\"The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.\":\"android.browser.errors.certificate\",\"Folder uploads are not available. Select individual files instead.\":\"android.files.errors.folderUnsupported\",\"Native speech requires a WebView with GET_COOKIE_INFO support. Update Android System WebView.\":\"android.speech.errors.cookieSupport\"},\"android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewEngine.kt\":{\"Update Android System WebView or Chrome to use separate instances.\":\"android.browser.errors.webviewProfiles\"},\"android/policy/src/main/kotlin/com/twidi/twicc/InstanceUrl.kt\":{\"Enter a valid HTTPS address.\":\"android.instances.errors.invalidAddress\",\"Use an HTTPS address, such as https://twicc.example.com.\":\"android.instances.errors.httpsRequired\",\"Use the server address without credentials, a query, or a fragment.\":\"android.instances.errors.originRequired\",\"Use the server root address, without a page path.\":\"android.instances.errors.rootRequired\",\"Enter a valid port number.\":\"android.instances.errors.invalidPort\"},\"android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt\":{\"Speech stopped. Start playback or turn on timeline reading again.\":\"backend.tts.sessionLost\",\"The audio stopped before the end\":\"session.speech.errors.cut\",\"Could not reach TwiCC\":\"session.speech.errors.unreachable\",\"Speech could not start. Reload the page and try again.\":\"session.speech.errors.startFailed\",\"Authentication expired. Open TwiCC and sign in again.\":\"backend.tts.authenticationExpired\",\"The active instance changed\":\"android.speech.errors.instanceChanged\",\"Speech failed\":\"android.speech.errors.failed\"}}"),
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
}, Dg = /* @__PURE__ */ new Map(), Og = /^-?(?:0|[1-9]\d*)(?:\.(\d+))?$/;
function kg(e, t) {
	if (typeof e == "number") {
		if (!Number.isFinite(e)) return e;
		let t = Object.is(e, -0) ? "-0" : String(e);
		if (!t.includes("e")) e = t;
		else {
			let [n, r] = t.split("e"), i = n.startsWith("-") ? "-" : "", a = n.replace("-", ""), o = (a.indexOf(".") < 0 ? a.length : a.indexOf(".")) + Number(r), s = a.replace(".", "");
			e = i + (o <= 0 ? `0.${"0".repeat(-o)}${s}` : o >= s.length ? s + "0".repeat(o - s.length) : `${s.slice(0, o)}.${s.slice(o)}`);
		}
	}
	if (typeof e != "string" || !Og.test(e)) return e;
	let [n, r] = e.split("."), i = n === "-0", a = new Intl.NumberFormat(t), o = a.format(i ? -0 : BigInt(n));
	return r === void 0 ? o : `${o}${a.formatToParts(1.1).find((e) => e.type === "decimal").value}${r}`;
}
function Ag(e, t, n) {
	let r = Eg.messages[e]?.numberFormats ?? {};
	return Object.fromEntries(Object.entries(t).map(([e, t]) => [e, r[e] === "decimal" ? kg(t, n) : t]));
}
function jg(e) {
	if (!Dg.has(e)) {
		let { ast: t } = wf(e, {
			jit: !0,
			optimize: !1,
			onError: (e) => {
				throw e;
			}
		}), n = t.body.type === 1 ? t.body.cases : [t.body];
		Dg.set(e, n.map((e) => e.items.filter((e) => e.type === 4).map((e) => e.key)));
	}
	return Dg.get(e);
}
function Mg(e, t = Cg.global) {
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
		let l = jg(c), u = Math.abs(typeof a.count == "number" ? a.count : typeof a.n == "number" ? a.n : e.count ?? -1), d = l.length === 1 ? 0 : l.length === 2 ? s === "fr" ? u < 2 ? 0 : 1 : u === 1 ? 0 : 1 : s === "fr" ? u === 0 ? 0 : u === 1 ? 1 : 2 : Math.min(u, 2);
		if (!l[d]) throw Error();
		for (let t of l[d]) {
			let n = o[t] ?? (["count", "n"].includes(t) ? e.count : void 0);
			if (n == null || Eg.messages[e.key]?.numberFormats?.[t] === "decimal" && !(typeof n == "number" || typeof n == "string" && Og.test(n))) throw Error();
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
function Ng(e, t) {
	if (e?.type === "local-ui-message-list") return e.messages.map((e) => Ng(e, t)).join(" — ");
	if (e?.type !== "local-ui-message") return e;
	let n = Object.fromEntries(Object.entries(e.params || {}).map(([e, n]) => [e, n?.type === "local-ui-message" || n?.type === "local-ui-message-list" ? Ng(n, t) : n])), r = typeof n.count == "number" ? n.count : typeof n.n == "number" ? n.n : e.count;
	for (let t of ["count", "n"]) n[t] == null && e.count !== void 0 && Eg.messages[e.key]?.numberFormats?.[t] && (n[t] = e.count);
	let i = Ag(e.key, n, t.locale.value);
	for (let e of ["count", "n"]) typeof i[e] == "number" && (i[e] = String(i[e]));
	return t.t(e.key, i, r === void 0 ? {} : r);
}
//#endregion
//#region src/i18n/uiMessages.js
function Pg(e, t = {}, n) {
	return {
		type: "local-ui-message",
		key: e,
		params: t,
		...n === void 0 ? {} : { count: n }
	};
}
function Fg(e) {
	return Ng(e, Cg.global);
}
function Ig(e, t = {}, n) {
	let r = Pg(e, t, n), i = Error(Fg(r));
	return Object.defineProperty(i, "message", {
		configurable: !0,
		get: () => Fg(r)
	}), i.uiMessage = r, i;
}
//#endregion
//#region src/i18n/remoteMessages.js
function Lg(e, t = e?.error ?? e?.message ?? "", n = "ui_message") {
	return Mg(e?.[n] ?? (n === "ui_message" ? e?.error_ui_message : void 0)) ?? t;
}
function Rg(e, t = e?.error ?? e?.message ?? "") {
	let n = Lg(e, e?.error ?? e?.message ?? t), r = n?.type === "local-ui-message" ? Ig(n.key, n.params, n.count) : Error(n);
	return e?.code !== void 0 && (r.code = e.code), e?.status !== void 0 && (r.status = e.status), r;
}
//#endregion
//#region src/utils/androidEnvironment.js
var zg = /* @__PURE__ */ xn(!1);
function Bg() {
	zg.value || (zg.value = !0, globalThis.window?.dispatchEvent(new Event("twicc:android-ready")));
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
	let n = /* @__PURE__ */ new Map(), r = /* @__PURE__ */ new Set(), i = Math.random().toString(36).slice(2), a = 0, o = !1, s = null, c = typeof e.TwiccAndroid?.postMessage == "function" ? e.TwiccAndroid : null;
	c && Bg();
	function l(e) {
		if (o) return;
		if (e?.protocol === 1 && e.event === "stateChanged") {
			for (let e of r) e();
			return;
		}
		if (!e || e.protocol !== 1 || typeof e.id != "string") return;
		let t = n.get(e.id);
		if (t && typeof e.ok == "boolean") {
			if (Bg(), n.delete(e.id), clearTimeout(t.timer), e.ok) t.resolve(e.state ?? null);
			else {
				let n = e.ui_message || typeof e.error == "string" ? Rg(e, Ig("common.android.errors.actionFailed").message) : Ig("common.android.errors.actionFailed");
				e.state !== void 0 && (n.state = e.state), t.reject(n);
			}
		}
	}
	function u(t) {
		t.source === e && t.origin === e.location.origin && (t.data?.channel === Vg && l(t.data.response), t.data?.channel === "twicc.android.environment" && t.data.protocol === 1 && typeof t.data.id == "string" && n.get(t.data.id)?.command === "getState" && Bg());
	}
	function d(e) {
		try {
			l(JSON.parse(e.data));
		} catch {}
	}
	return e.addEventListener("message", u), c && (c.onmessage = d), {
		onStateChanged(e) {
			return o || r.add(e), () => r.delete(e);
		},
		async request(r, l = {}) {
			if (o) return Promise.reject(Ig("common.android.errors.closed"));
			if (c && Wg.has(r)) {
				let n = Date.now() + t;
				for (; !s && !o;) {
					let t = e.__twiccNativeTtsDocument;
					if (typeof t == "string" && t) {
						s = t;
						break;
					}
					if (Date.now() >= n) throw Ig(r === "locale.set" ? "android.language.errors.staleDocument" : "common.android.errors.speechPreparing");
					await new Promise((e) => setTimeout(e, 10));
				}
				if (o) throw Ig("common.android.errors.closed");
			}
			let u = `${i}-${++a}`, d = {
				...l,
				...s && Wg.has(r) ? { documentToken: s } : {},
				protocol: 1,
				id: u,
				command: r
			}, f;
			try {
				f = JSON.stringify(d);
				let e = Ug.has(r) ? 12582912 : 16384;
				if (new TextEncoder().encode(f).byteLength > e) return Promise.reject(Ig("common.android.errors.tooLarge"));
			} catch (e) {
				return console.warn("Android request serialization failed", e), Promise.reject(Ig("common.android.errors.actionFailed"));
			}
			return new Promise((i, a) => {
				let o = setTimeout(() => {
					n.delete(u), a(Ig("common.android.errors.unreachable"));
				}, t);
				n.set(u, {
					resolve: i,
					reject: a,
					timer: o,
					command: r
				});
				try {
					c ? c.postMessage(f) : e.postMessage({
						channel: Hg,
						request: d
					}, e.location.origin);
				} catch (e) {
					clearTimeout(o), n.delete(u), console.warn("Android request transport failed", e), a(Ig("common.android.errors.unreachable"));
				}
			});
		},
		close() {
			o = !0, r.clear(), e.removeEventListener("message", u), c?.onmessage === d && (c.onmessage = null);
			for (let e of n.values()) clearTimeout(e.timer), e.reject(Ig("common.android.errors.closed"));
			n.clear();
		}
	};
}
var Kg;
function qg() {
	return Kg ?? (Kg = Gg(window));
}
//#endregion
//#region src/i18n/androidLocale.js
var Jg = gg.map(({ value: e }) => e);
function Yg(e) {
	return typeof e?.locale == "string" && Number.isSafeInteger(e.revision) && e.revision >= 0 && Array.isArray(e.supportedLocales) && e.supportedLocales.includes(e.locale) && e.supportedLocales.every((e) => typeof e == "string");
}
function Xg({ client: e, document: t, applyLocale: n, readBrowserLocale: r, onCompatibility: i = () => {} }) {
	let a = /* @__PURE__ */ ln({
		mode: "browser",
		locale: "en",
		revision: null,
		supportedLocales: [...Jg]
	}), o, s, c = !1, l = !1, u, d = 0, f = 0, p = Promise.resolve(), m = 0, h, g, _ = !0, v = !1;
	function y(e) {
		i(e ? Pg(e) : null);
	}
	async function b(e, t, r = !1) {
		if (c || t !== d) return;
		let i = _g(e) ? e : "en";
		if (h?.locale === i) {
			if (h.status === "pending") return h.promise;
			if (h.status === "success" || !r) return;
		}
		let a = {
			locale: i,
			status: "pending",
			renderedLocale: null,
			promise: null
		};
		h = a, a.promise = Promise.resolve().then(() => n(i)).then((e) => {
			a.renderedLocale = e ?? i, a.status = a.renderedLocale === i ? "success" : "fallback";
		}, (e) => {
			throw a.status = "failed", e;
		}), await a.promise;
	}
	async function x(e, t, n = !1) {
		Yg(e) && !c && (u && e.revision < u.revision || (u = {
			...e,
			supportedLocales: [...e.supportedLocales]
		}, t === d && (Object.assign(a, u, { mode: _g(e.locale) ? "native" : "incompatible" }), y(a.mode === "incompatible" ? "android.language.errors.updateServer" : null), await b(e.locale, t, n))));
	}
	function S() {
		c || (_ ? v = !0 : E().catch((e) => {
			!c && a.mode !== "incompatible" && i(e.uiMessage || Pg("common.android.errors.unreachable"));
		}));
	}
	function C() {
		l && t.visibilityState === "visible" && S();
	}
	function w() {
		return o ?? (o = (async () => {
			let n = ++d;
			s = e.onStateChanged(S), t?.addEventListener("visibilitychange", C);
			let o;
			try {
				o = await e.request("getState");
			} catch {}
			if (c) return a;
			if (o?.capabilities?.appLanguage === 1 && Yg(o.appLanguage)) for (l = !0, v = !0; v && !c;) {
				v = !1;
				let t = ++d, n;
				try {
					n = await e.request("locale.get");
				} catch (e) {
					await x(u || o.appLanguage, ++d), !c && a.mode !== "incompatible" && i(e.uiMessage || Pg("common.android.errors.unreachable"));
					break;
				}
				await x(n, t);
			}
			else {
				a.mode = o ? "incompatible" : "browser";
				let e = r();
				a.locale = _g(e) ? e : "en", y(o ? "android.language.errors.updateApp" : null), await b(a.locale, n);
			}
			return _ = !1, v && !c && (v = !1, S()), a;
		})());
	}
	function T() {
		return g ?? (g = (async () => {
			let t;
			try {
				t = await e.request("getState");
			} catch {
				return;
			}
			if (c) return;
			if (t?.capabilities?.appLanguage !== 1 || !Yg(t.appLanguage)) {
				t && (a.mode = "incompatible", y("android.language.errors.updateApp"));
				return;
			}
			l = !0, ++f;
			let n = ++d;
			try {
				await x(await e.request("locale.get"), n);
			} catch (e) {
				await x(u || t.appLanguage, n), !c && a.mode !== "incompatible" && i(e.uiMessage || Pg("common.android.errors.unreachable"));
			}
			a.revision === null && u && await x(u, d), c || Bg();
		})().finally(() => {
			g = null;
		}));
	}
	async function E() {
		if (await w(), c) return;
		if (!l) return T();
		let t = ++d;
		await x(await e.request("locale.get"), t);
	}
	async function D(t) {
		let n = ++f;
		if (await w(), g && await g, c || n !== f) return;
		if (!l) {
			let e = ++d;
			a.locale = _g(t) ? t : "en", await b(a.locale, e, !0);
			return;
		}
		let r = u.revision, o = m ? p : Promise.resolve();
		++m;
		let s = o.then(async (a) => {
			if (c || n !== f) return a;
			let o = ++d;
			try {
				let i = await e.request("locale.set", {
					locale: t,
					expectedRevision: a?.revision ?? r
				});
				return {
					revision: i.revision,
					rendering: x(i, n === f ? o : -1, !0)
				};
			} catch (e) {
				++f, e.state && await x(e.state, ++d);
				try {
					await E();
				} catch {}
				throw c || i(e.uiMessage || Pg("android.language.errors.changed")), e;
			}
		});
		p = s.then((e) => (--m, e), () => {
			--m;
		}), await (await s)?.rendering;
	}
	function O() {
		c = !0, ++d, ++f, s?.(), t?.removeEventListener("visibilitychange", C);
	}
	return {
		initialize: w,
		select: D,
		reconcile: E,
		dispose: O
	};
}
var Zg = /* @__PURE__ */ Sn(null), Qg = /* @__PURE__ */ Sn(null), $g;
function e_() {
	return $g || ($g = Xg({
		client: qg(),
		document: globalThis.document,
		applyLocale: (e) => Tg(e),
		readBrowserLocale: vg,
		onCompatibility: (e) => {
			Qg.value = e;
		}
	}), globalThis.window?.addEventListener("pagehide", (e) => {
		e.persisted || $g.dispose();
	})), $g;
}
async function t_() {
	let e = await e_().initialize();
	return Zg.value = e, (e.mode === "native" || e.mode === "incompatible" && e.revision !== null) && Bg(), e;
}
async function n_(e) {
	await t_(), await e_().select(e);
}
//#endregion
export { Xg as createAndroidLocaleController, t_ as initializeInterfaceLocale, Qg as interfaceLocaleCompatibility, Zg as interfaceLocaleState, n_ as selectInterfaceLocale };
