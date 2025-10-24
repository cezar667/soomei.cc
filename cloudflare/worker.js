// Router curto com preflight: /r/{uid}
// KV:
//   card:{uid} = { status: "pending|active|blocked", vanity?: "slug", updated_at, blocked_reason? }

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const p = url.pathname.split("/").filter(Boolean);
    const apiBase = (env.API_BASE || `https://${url.host}`).replace(/\/+$/, '');

    if (p.length === 2 && p[0] === "r") {
      const uid = p[1];
      const raw = await env.CARDS.get(`card:${uid}`);
      if (!raw) {
        return Response.redirect(`${apiBase}/onboard/${uid}`, 302);
      }
      let card = {};
      try { card = JSON.parse(raw); } catch (_) {}

      // métrica assíncrona (opcional)
      try {
        ctx.waitUntil(env.TAPS.send(JSON.stringify({
          uid,
          slug: card.vanity || uid,
          ts: Date.now(),
          ip: request.headers.get("CF-Connecting-IP"),
          ua: request.headers.get("User-Agent"),
          referrer: request.headers.get("Referer"),
        })));
      } catch (_) {}

      if (card.status === "blocked") {
        return Response.redirect(`${apiBase}/blocked`, 302);
      }
      if (!card.status || card.status === "pending") {
        return Response.redirect(`${apiBase}/onboard/${uid}`, 302);
      }
      const dest = card.vanity ? `/${card.vanity}` : `/${uid}`;
      return Response.redirect(`${apiBase}${dest}`, 302);
    }
    return new Response("not found", { status: 404 });
  }
}
