import { loadSiteConfig, fact, whatsappLink } from "../lib/site-config";
import BookingForm from "./booking-form";

export const dynamic = "force-dynamic";

export default function Page() {
  const config = loadSiteConfig();
  const name = fact(config.business_name) || "Preview";
  const description = fact(config.description);
  const address = fact(config.address);
  const hours = fact(config.hours);
  const phone = fact(config.phone);
  const email = fact(config.email);
  const whatsapp = whatsappLink(config.whatsapp);
  const bookingHref = config.booking_url || "#marcacao";
  const services = Array.isArray(config.services) ? config.services : [];
  const cityLabel = config.city
    ? String(config.city).charAt(0).toUpperCase() + String(config.city).slice(1)
    : "";

  return (
    <>
      {config.mode === "preview" && (
        <div className="preview-banner">
          Conceito preparado pela AI Lead Pipeline — dados verificados em fontes públicas · noindex
        </div>
      )}

      <header className="hero">
        <div className="hero-inner">
          {cityLabel && <div className="city-line">{cityLabel}</div>}
          <h1 className="brand-mark">{name}</h1>
          {description && <p className="support">{description}</p>}
          <a className="cta" href={bookingHref}>
            Marcar agora
          </a>
        </div>
      </header>

      <div className="wrap">
        {services.length > 0 && (
          <section className="section" id="servicos">
            <div className="section-label">Serviços</div>
            <h2>Tratamentos e cuidados</h2>
            <p className="section-lead">Escolha a experiência certa para o seu ritmo — preços só quando verificados.</p>
            <div className="services">
              {services.map((service, index) => (
                <div className="service" key={index}>
                  <strong>{fact(service.name)}</strong>
                  {service.price && fact(service.price) ? (
                    <div className="price">{fact(service.price)}</div>
                  ) : (
                    <div />
                  )}
                  {service.description ? <div className="desc">{service.description}</div> : null}
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="section" id="contactos">
          <div className="section-label">Contactos</div>
          <h2>Fale connosco</h2>
          <div className="contact-grid">
            {phone && (
              <div className="contact-item">
                <div className="k">Telefone</div>
                <div className="v">
                  <a href={`tel:${phone.replace(/\s/g, "")}`}>{phone}</a>
                </div>
              </div>
            )}
            {whatsapp && (
              <div className="contact-item">
                <div className="k">WhatsApp</div>
                <div className="v">
                  <a href={whatsapp}>Abrir conversa</a>
                </div>
              </div>
            )}
            {email && (
              <div className="contact-item">
                <div className="k">Email</div>
                <div className="v">
                  <a href={`mailto:${email}`}>{email}</a>
                </div>
              </div>
            )}
            {address && (
              <div className="contact-item">
                <div className="k">Morada</div>
                <div className="v">{address}</div>
              </div>
            )}
            {hours && (
              <div className="contact-item">
                <div className="k">Horário</div>
                <div className="v">{hours}</div>
              </div>
            )}
            {Object.entries(config.socials || {}).map(([network, url]) => (
              <div className="contact-item" key={network}>
                <div className="k">{network}</div>
                <div className="v">
                  <a href={url}>Ver perfil</a>
                </div>
              </div>
            ))}
          </div>
        </section>

        {!config.booking_url && (
          <section className="section" id="marcacao">
            <div className="section-label">Marcação</div>
            <h2>Peça a sua sessão</h2>
            <p className="section-lead">Deixe o contacto — respondemos para confirmar horário e tratamento.</p>
            <div className="booking-panel">
              <BookingForm
                endpoint={config.form_endpoint}
                buildId={config.build_id || ""}
                formToken={config.form_token || ""}
              />
            </div>
          </section>
        )}
      </div>

      <footer className="site-footer">
        {config.mode === "preview" ? (
          <>Conceito AI Lead Pipeline · site temporário, não indexado, com expiração automática</>
        ) : (
          <>© {new Date().getFullYear()} {name}</>
        )}
      </footer>
    </>
  );
}
