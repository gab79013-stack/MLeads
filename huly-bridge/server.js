const http = require("http");
const { execSync } = require("child_process");

const PORT = 3333;

const server = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/sync-lead") {
    let body = "";
    req.on("data", chunk => { body += chunk; });
    req.on("end", () => {
      try {
        const lead = JSON.parse(body);
        const result = formatLead(lead);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
  } else {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "huly-bridge running", port: PORT }));
  }
});

function formatLead(lead) {
  const trade = lead.ai_trade || lead._trade || "GENERAL";
  const score = lead.score || lead._scoring?.score || 0;
  const urgency = lead.ai_urgency || lead._urgency || "MEDIUM";
  const contractor = lead.contractor || "";
  const phone = lead.contact_phone || "";
  const value = lead.value_float || 0;
  const address = lead.address || "";
  const city = lead.city || "";
  const desc = (lead.description || "").substring(0, 200);
  const aiSummary = lead.ai_summary || "";
  const painPoint = lead.ai_key_pain_point || "";
  const upsell = lead.ai_upsell_opportunity || "";
  const bestTime = lead.ai_best_contact_time || "";
  const originalTrade = lead.original_trade || "";
  const isSelfPull = lead.is_gc_self_pull || false;

  const prefix = score >= 90 ? "🔥" : score >= 70 ? "🌡️" : "";
  const projectName = `${prefix} [${trade}] ${address.substring(0, 50)}`;

  let projectDesc = `📍 ${address}, ${city}\n🎯 Score: ${score}/100 | Urgency: ${urgency}`;
  if (isSelfPull) projectDesc += `\n⚠️ Self-pull (original: ${originalTrade})`;
  if (contractor) projectDesc += `\n🏗️ GC: ${contractor}`;
  if (phone) projectDesc += `\n📞 ${phone}`;
  if (value) projectDesc += `\n💰 $${value.toLocaleString()}`;
  if (desc) projectDesc += `\n\n📋 ${desc}`;
  if (aiSummary) projectDesc += `\n🤖 ${aiSummary}`;
  if (painPoint) projectDesc += `\n💡 ${painPoint}`;
  if (upsell) projectDesc += `\n💰 Upsell: ${upsell}`;
  if (bestTime && bestTime !== "ANY") projectDesc += `\n🕑 Best: ${bestTime}`;

  const tasks = [];
  if (urgency === "HIGH") {
    tasks.push({ title: "📞 Call GC ASAP", description: `Call ${contractor} at ${phone}` });
    tasks.push({ title: "📧 Send intro email", description: "Send introduction with company info" });
  } else if (urgency === "MEDIUM") {
    tasks.push({ title: "📞 Contact this week", description: `Call ${contractor} at ${phone}` });
  } else {
    tasks.push({ title: "📞 Follow up", description: `Contact ${contractor} at ${phone}` });
  }
  if (upsell) tasks.push({ title: `💡 Offer: ${upsell.substring(0, 50)}`, description: upsell });
  if (bestTime && bestTime !== "ANY") tasks.push({ title: `🕐 Best time: ${bestTime}`, description: `Call during ${bestTime}` });

  return {
    project_name: projectName,
    project_description: projectDesc,
    tasks,
    lead_id: lead.id || lead.address_key,
    trade,
    score,
    huly_deep_link: `http://45.32.89.38:8080`,
  };
}

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Huly Bridge running on port ${PORT}`);
});
