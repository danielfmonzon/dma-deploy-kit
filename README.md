# dma-deploy-kit

A config-driven deployment kit for shipping bilingual (English/Spanish) AI voice receptionists built on [Retell](https://www.retellai.com/) and [ElevenLabs](https://elevenlabs.io/).

## Overview

**The problem.** Small and mid-size businesses miss calls — after hours, during the lunch rush, when the front desk is already on another line. Every missed call is a missed booking, a lost lead, or a frustrated customer. For businesses serving bilingual communities, the gap is even wider: a caller who reaches an English-only line and needs Spanish (or vice versa) often just hangs up. Building a competent voice agent for each business is possible, but doing it by hand for every new client means re-solving the same problems — prompt wiring, voice selection, call routing, post-call follow-up — over and over, with no shared foundation and no way to keep quality consistent as the roster grows.

**The solution.** `dma-deploy-kit` turns "stand up a bilingual AI receptionist for a new client" into a repeatable, config-driven deployment. Each client is described by a single configuration file; the kit reads that config and provisions a Retell-backed conversational agent with ElevenLabs voices for both English and Spanish, wires up call routing, and handles post-call actions. Business logic lives in code that's shared across every deployment, so improvements ship to everyone at once — while each client's specifics (greeting, hours, routing rules, voices, escalation paths) stay in their own private config. Onboarding a new receptionist becomes editing a file, not rebuilding an agent.

**Who it's for.** This kit is built for DMA to deploy and operate bilingual AI voice receptionists for its clients — service businesses (clinics, dealerships, home services, professional offices, and similar) that take inbound calls and can't afford to let them go unanswered, especially where callers move between English and Spanish.

**Origin.** DMA built this kit after deploying voice receptionists one client at a time and watching the same work repeat with every new engagement. The insight was that the *differences* between clients are narrow and describable — a greeting, business hours, how calls should be routed, which voice, what happens after the call — while the *hard parts* are shared. Factoring the shared parts into a kit and pushing the differences into config makes each new deployment faster, more consistent, and easier to improve over time.

## Architecture

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Features

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Tech stack

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Repo structure

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Getting started

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Example workflow

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Design decisions

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## AI

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Developer experience

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Roadmap

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Lessons learned

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->

## Case studies

<!-- CODE-BACKED: write after this capability ships. Do not fill from memory. -->
