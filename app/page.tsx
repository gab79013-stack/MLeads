import { SiteHeader } from "@/components/site-header"
import { Hero } from "@/components/hero"
import { StormDamage } from "@/components/storm-damage"
import { GcOpportunities } from "@/components/gc-opportunities"
import { PipelineCrm } from "@/components/pipeline-crm"
import { VerifiedSources } from "@/components/verified-sources"
import { Cta } from "@/components/cta"
import { SiteFooter } from "@/components/site-footer"

export default function Page() {
  return (
    <>
      <SiteHeader />
      <main>
        <Hero />
        <StormDamage />
        <GcOpportunities />
        <PipelineCrm />
        <VerifiedSources />
        <Cta />
      </main>
      <SiteFooter />
    </>
  )
}
