#!/usr/bin/env python3
"""
aprovar_changes.py (legacy — DESATIVADO 02/07/2026)

Regra anterior: Bianca colocava :white_check_mark: na thread do briefing no canal
→ script varria conversations_replies → aplicava no SmartSheet.

Nova regra (Bianca 02/07/2026, conversa DM):
  - fechamento_diario.py envia UMA DM por tarefa pra Bianca
  - Bianca reage :white_check_mark: AQUI NA DM
  - agente desta sessão aplica no SmartSheet imediatamente
  - Não há mais script standalone de aprovação

Mantido como no-op silencioso pra não quebrar o cron job a73f113395f8
(executado mas sem efeito). Pode ser removido em refator futura.
"""

import logging
log = logging.getLogger(__name__)

if __name__ == "__main__":
    log.info("aprovar_changes.py desativado. Aprovações são tratadas na DM (agente).")
