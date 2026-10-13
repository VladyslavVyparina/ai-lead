"use client";

import { useState } from "react";

/**
 * Preview mode: submissions go to the controlled operator endpoint on the
 * backend (POST /preview-form), never to the business (§5.3). Production
 * routing is activated only after sale.
 */
export default function BookingForm({ endpoint, buildId, formToken }) {
  const [status, setStatus] = useState("idle");
  const [errorDetail, setErrorDetail] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    setStatus("sending");
    setErrorDetail("");
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          build_id: Number(buildId),
          form_token: formToken,
          name: form.get("name"),
          contact: form.get("contact"),
          message: form.get("message"),
        }),
      });
      if (response.ok) {
        setStatus("ok");
        event.target.reset();
        return;
      }
      let detail = `Erro ${response.status}`;
      try {
        const data = await response.json();
        if (typeof data?.detail === "string") detail = data.detail;
        else if (Array.isArray(data?.detail)) detail = data.detail[0]?.msg || detail;
      } catch {
        /* ignore non-JSON bodies */
      }
      setErrorDetail(detail);
      setStatus("error");
    } catch {
      setErrorDetail("Sem ligação ao servidor (CORS ou endpoint errado)");
      setStatus("error");
    }
  }

  return (
    <form className="booking" onSubmit={handleSubmit}>
      <input name="name" placeholder="O seu nome" required />
      <input name="contact" placeholder="Telefone ou email" required />
      <textarea name="message" placeholder="Serviço e horário pretendido" rows={3} />
      <button type="submit" disabled={status === "sending"}>
        {status === "sending" ? "A enviar…" : "Enviar pedido"}
      </button>
      {status === "ok" && <div className="form-status ok">Pedido recebido — entraremos em contacto.</div>}
      {status === "error" && (
        <div className="form-status err">
          Não foi possível enviar{errorDetail ? `: ${errorDetail}` : ""}.
        </div>
      )}
    </form>
  );
}
