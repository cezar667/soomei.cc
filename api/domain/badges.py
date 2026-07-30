from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BadgeDefinition:
    type: str
    label: str
    eyebrow: str
    brand: str
    title: str
    description: str
    note: str
    css_modifier: str
    default_days: int


BADGE_CATALOG: dict[str, BadgeDefinition] = {
    "soomei_connector": BadgeDefinition(
        type="soomei_connector",
        label="Destaque Soomei",
        eyebrow="Destaque",
        brand="Soomei",
        title="Perfil em Destaque Soomei",
        description=(
            "Este cartão recebeu uma chancela temporária de visibilidade da Soomei "
            "por participação, indicação qualificada ou benefício ativo na rede."
        ),
        note=(
            "Na prática, é um sinal de presença ativa: a pessoa está movimentando "
            "conexões, oportunidades e relacionamento dentro do ecossistema Soomei."
        ),
        css_modifier="spotlight",
        default_days=30,
    ),
    "founding_member": BadgeDefinition(
        type="founding_member",
        label="Associado Fundador",
        eyebrow="Associado",
        brand="Fundador",
        title="Associado Fundador Soomei",
        description=(
            "Este perfil faz parte do grupo pioneiro de associados que acreditou "
            "na Soomei desde o início e ajudou a dar forma à comunidade."
        ),
        note=(
            "O selo Fundador é uma distinção permanente de origem e pertencimento. "
            "Ele reconhece quem esteve presente nos primeiros capítulos da Soomei."
        ),
        css_modifier="founder",
        default_days=36500,
    ),
    "community_ambassador": BadgeDefinition(
        type="community_ambassador",
        label="Embaixador Soomei",
        eyebrow="Embaixador",
        brand="Soomei",
        title="Embaixador da Comunidade",
        description="Reconhecimento por representar e fortalecer a comunidade Soomei.",
        note="Este associado aproxima pessoas, iniciativas e oportunidades dentro do ecossistema.",
        css_modifier="ambassador",
        default_days=365,
    ),
    "community_mentor": BadgeDefinition(
        type="community_mentor",
        label="Mentor da Comunidade",
        eyebrow="Mentor",
        brand="Soomei",
        title="Mentor da Comunidade Soomei",
        description="Reconhecimento por compartilhar experiência e apoiar outros empreendedores.",
        note="Mentores contribuem ativamente para o desenvolvimento coletivo da comunidade.",
        css_modifier="mentor",
        default_days=365,
    ),
    "verified_partner": BadgeDefinition(
        type="verified_partner",
        label="Parceiro Verificado",
        eyebrow="Parceiro",
        brand="Verificado",
        title="Parceiro Verificado Soomei",
        description="Este perfil representa um parceiro reconhecido no ecossistema Soomei.",
        note="A verificação identifica a relação com a rede; não substitui certificações profissionais.",
        css_modifier="partner",
        default_days=365,
    ),
}


def get_badge_definition(badge_type: str) -> BadgeDefinition | None:
    return BADGE_CATALOG.get((badge_type or "").strip())


def choose_badge_type(available_types: list[str], selected_type: str | None) -> str | None:
    available = [value for value in available_types if value in BADGE_CATALOG]
    selected = (selected_type or "").strip()
    if selected in available:
        return selected
    if "soomei_connector" in available:
        return "soomei_connector"
    return available[0] if available else None
