/**
 * Bundled articles: illustrative sample content used when neither the gateway
 * feed nor the profile supplies articles (see hooks/useArticles.ts). The
 * `Article` type is the shape both sources are mapped to.
 */
export type Article = {
  id: string;
  date: string;
  category: string;
  author: string;
  tags: string[];
  title: string;
  summary: string;
  body: string;   // HTML, rendered via dangerouslySetInnerHTML
};

export const ARTICLES: Article[] = [
  { id:'a-001', date:'2026-06-14', category:'PHEM', author:'EPHI Surveillance', tags:['cholera','Somali'],
    title:'Cholera outbreak response: Somali Region update (Week 24)',
    summary:'247 cumulative cases across four Somali woredas, 38 in the past week. OCV and WASH response underway.',
    body:'<p>Following confirmation of cholera cases in <strong>Doolo and Korahe zones</strong>, the Public Health Emergency Management (PHEM) directorate has mobilised four Rapid Response Teams in coordination with the Regional Health Bureau.</p>'
        +'<p>The first cases were traced to shared water points along seasonal migration routes, where pastoralist households draw from the same unprotected sources as their herds. Early rains have widened the risk of onward spread, and the Somali Regional Health Bureau has asked woreda health offices in the four affected districts to step up active case-finding at health posts and among mobile communities.</p>'
        +'<p>As of 14 June, the cumulative case count stands at <strong>247</strong>, with 38 cases reported in the last seven days. The case-fatality rate remains under 1.5%. Oral cholera vaccine (OCV) campaigns have launched in three woredas; WASH interventions including chlorinated water points are operational in 21 sites.</p>'
        +'<p>Two cholera treatment centres and five oral rehydration points are now running, staffed by regional clinical teams with deployed EPHI officers alongside them. Case management follows the national protocol: severe cases are stabilised on intravenous fluids before referral, while milder cases are treated with oral rehydration salts and zinc close to home.</p>'
        +'<p>The OCV round targets everyone aged one year and above across the three highest-burden woredas, an estimated 310,000 people. Health extension workers are moving house to house with the vaccination teams and using the same visits to explain safe water storage, handwashing and the importance of seeking care early.</p>'
        +'<p>On water and sanitation, partners are trucking treated water to settlements whose boreholes returned contaminated samples, and household treatment sachets are reaching families through the health posts. Clan and religious leaders have joined the risk-communication sessions to counter rumours and to encourage households to bring sick relatives for treatment rather than manage them at home.</p>'
        +'<p>EPHI continues to coordinate with partners including UNICEF, WHO, and MSF for surge support. The next situation report will publish on 21 June (Week 25).</p>' },
  { id:'a-002', date:'2026-06-11', category:'Press', author:'FMoH Communications', tags:['HSTP-III'],
    title:'FMoH launches HSTP-III consultations',
    summary:'National consultations open for HSTP-III (2025/26 to 2029/30); regional rounds run through August.',
    body:'<p>His Excellency the Minister of Health officially launched the consultation process for the next five-year transformation plan today at the Skylight Hotel in Addis Ababa.</p>'
        +'<p>Opening the meeting, the Minister set the new plan against the record of HSTP-II. Ethiopia widened primary care and held essential services steady through drought, conflict and the pressures of recent years, but gains in maternal survival, immunisation and equity have been uneven between regions. HSTP-III is meant to close those gaps rather than restate old ambitions.</p>'
        +'<p>HSTP-III builds on the lessons of HSTP-II and orients the sector around three pillars: <strong>quality of care</strong>, <strong>equity in coverage</strong>, and <strong>digital transformation</strong>. Regional consultations will run through August 2026.</p>'
        +"<p>Each pillar carries a concrete agenda. Quality of care looks at what happens once a patient reaches a facility, from safe childbirth to correct treatment. Equity turns attention to the populations the system reaches last, among them pastoralist and remote communities and the urban poor. Digital transformation ties the country's health information systems together so that a woreda's data can inform a national decision, building on the eCHIS platform and routine DHIS2 reporting.</p>"
        +'<p>The consultation is designed to reach beyond the capital. Regional Health Bureaus will host workshops down to zonal and woreda level, with seats for health extension workers, professional associations, the private sector and academia. Sessions in the pastoralist regions will be scheduled around seasonal movement so that voices from Afar, Somali and the lowland woredas are not lost.</p>'
        +'<p>Financing runs through the whole discussion. Officials pointed to community-based health insurance, growing domestic resource mobilisation and continued partner support from the World Bank, WHO and bilateral donors as the base on which the plan must be affordable, not aspirational.</p>'
        +'<p>Civil society, professional associations, partners, and citizens are invited to submit input through the FMoH portal. The full draft will be published for public review in November.</p>' },
  { id:'a-003', date:'2026-06-08', category:'Advisory', author:'EPI Directorate', tags:['immunisation','Penta3','pastoralist'],
    title:'Routine immunisation campaign: June 16 to 22',
    summary:'One-week catch-up campaign for under-5s across Afar, Somali, Benishangul-Gumuz and Gambella, 16 to 22 June.',
    body:'<p>The Expanded Programme on Immunization (EPI) is conducting a one-week catch-up campaign across Afar, Somali, Benishangul-Gumuz, and Gambella regions from <strong>16 to 22 June 2026</strong>.</p>'
        +"<p>These four regions were chosen because routine immunisation coverage there sits below the national average, and because they hold a large share of the country's zero-dose children, infants who have never received a single vaccine. Long distances, sparse health posts and seasonal movement all make it harder for families to complete the schedule on time.</p>"
        +'<p>Antigens included: BCG, Penta1-3, MCV1, MCV2, IPV, PCV, rotavirus. The campaign uses outreach teams covering 412 sites including health posts, mobile clinics, and school grounds.</p>'
        +'<p>Vaccines are being moved under an unbroken cold chain, with solar refrigerators at health centres and cold boxes carried to the furthest kebeles. Mobile teams will travel to grazing areas and river crossings to reach households that rarely see a static facility, and children will also be offered vitamin A supplementation and deworming during the same visit.</p>'
        +'<p>Health extension workers and community volunteers have spent the past fortnight mobilising families, mapping which children are due and passing the word through kebele leaders and religious institutions. Each site keeps a defaulter register so that a child missed on the first pass is followed up before the week closes.</p>'
        +"<p>Caregivers are encouraged to bring their child's immunisation card. Lost cards can be reissued at the site.</p>"
        +'<p>Regional Health Bureaus will report daily through DHIS2 so that supervisors can see which woredas are lagging and redirect teams while the campaign is still running. A coverage assessment in the weeks that follow will show how many previously unreached children were found, and where a further round may be needed.</p>' },
  { id:'a-004', date:'2026-06-03', category:'PHEM', author:'EPHI Surveillance', tags:['measles','Tigray'],
    title:'Measles outbreak in Tigray Region declared contained',
    summary:'Tigray measles outbreak contained after 12 weeks: 1,847 cases, 11 deaths, post-response coverage above 92%.',
    body:'<p>The PHEM weekly bulletin confirms that the measles outbreak that began in central Tigray in March has been declared contained as of Week 22.</p>'
        +'<p>The outbreak took hold in a cohort of children whose routine immunisation had been interrupted during the difficult years in the region, leaving pockets of low immunity into which the virus spread quickly. Measles finds these gaps first, which is why rebuilding routine coverage sits at the centre of the response.</p>'
        +'<p>The outbreak affected 18 woredas with <strong>1,847 confirmed cases</strong> and 11 deaths (CFR 0.6%). The Rapid Response Team conducted three reactive vaccination rounds reaching 280,000 children.</p>'
        +'<p>Containment was declared only after the affected woredas went two consecutive incubation periods with no new laboratory-confirmed cases, with samples tested at the EPHI national reference laboratory. The Regional Health Bureau maintained active case-finding throughout that window rather than relying on passive reporting alone.</p>'
        +'<p>Clinically, cases were managed with vitamin A and close attention to complications such as pneumonia and diarrhoea, which drive most measles deaths. Undernourished children were the most vulnerable, and treatment centres worked with nutrition teams to identify and refer them early.</p>'
        +'<p>Attention now turns to prevention. The Regional Health Bureau is running catch-up sessions to reach children missed during the outbreak, with particular focus on the second dose of measles-containing vaccine (MCV2), and is working to restore the routine schedule so that immunity does not fall away again once the emergency response ends.</p>'
        +'<p>Routine surveillance continues; the next risk assessment is scheduled for Week 26.</p>' },
  { id:'a-005', date:'2026-05-28', category:'Press', author:'FMoH Director-General', tags:['HEARTS','NCD','hypertension'],
    title:'HEARTS programme expands to 142 additional health centres',
    summary:'HEARTS hypertension package adds 142 health centres, reaching 511 across 11 regions.',
    body:'<p>The Federal Ministry of Health announced today the expansion of the HEARTS programme (a WHO-led standardised approach to managing hypertension in primary care) to <strong>142 additional health centres</strong>.</p>'
        +'<p>Non-communicable diseases now account for a rising share of illness and death in Ethiopia, and raised blood pressure is among the most common and the most quietly dangerous. Many people carry it for years without symptoms until it surfaces as a stroke or heart attack, which is why moving detection and treatment into everyday primary care matters.</p>'
        +'<p>The expansion targets areas where HTN screening coverage has been below 30%. Each facility receives a kit including digital BP monitors, validated training, simplified treatment protocols, and a 6-month medication starter package.</p>'
        +'<p>Alongside the equipment, staff at each site are trained on the standard treatment protocol and supported through follow-up mentoring. The model relies on task-sharing, so that nurses and health officers, not only physicians, can start and adjust treatment using clear step-by-step guidance rather than case-by-case improvisation.</p>'
        +'<p>Keeping patients on treatment is the harder half of the work. Facilities hold a hypertension register to track who is due for review, and continuity of the medicine supply through EPSS is central, since a stockout at the pharmacy quickly becomes a patient lost from care. Enrolment in community-based health insurance helps shield households from the cost of long-term treatment.</p>'
        +'<p>This brings total HEARTS coverage to 511 health centres across 11 regions, on track for the HSTP-II target of 600 centres by end of 2026.</p>'
        +'<p>Health extension workers support the programme in the community, referring people with high readings and helping those already diagnosed keep their appointments. The Ministry will judge the expansion less by the number of centres than by how many enrolled patients reach controlled blood pressure, the outcome the programme ultimately exists to move.</p>' },
  { id:'a-006', date:'2026-05-22', category:'Advisory', author:'EFDA', tags:['cold chain','safety','EPI'],
    title:'Cold-chain integrity advisory: BCG lot 2024-LOT-39812',
    summary:'BCG lot 2024-LOT-39812 quarantined after cold-chain excursions at seven facilities; re-test pending.',
    body:'<p>The Ethiopian Food and Drug Authority (EFDA), in coordination with EPI, has issued a cold-chain integrity advisory for BCG vaccine lot <strong>2024-LOT-39812</strong>.</p>'
        +'<p>The excursions came to light through routine temperature monitoring at the affected facilities, where electronic loggers and vaccine-fridge records showed readings outside the safe range. Staff flagged the breaches through the standard reporting channel, which triggered this advisory.</p>'
        +'<p>Temperature monitoring at seven facilities recorded excursions above 8°C for periods longer than 4 hours. Affected doses (estimated 2,118) have been quarantined and will be re-tested for potency.</p>'
        +'<p>EFDA stresses that this is a precautionary measure about potency, not safety. Children who received doses from this lot are not at risk of harm; the concern is that heat-exposed vaccine may be less effective, and any child found to have received a compromised dose can simply be offered BCG again once testing is complete.</p>'
        +'<p>Until the re-test results are in, facilities holding stock from the affected lot are instructed to set those doses aside and not administer them, and to record the lot numbers of any doses already given so that affected children can be traced if revaccination proves necessary.</p>'
        +'<p>Facilities should follow the standard quarantine protocol and report through the AEFI surveillance system. New stock from a different lot will be redistributed within 5 days.</p>'
        +'<p>EFDA and EPI are also examining why the cold chain failed at these sites, whether through power interruption, refrigerator fault or handling error, so that the fix goes beyond replacing the stock. Findings will feed into refresher training on cold-chain management for the facilities involved.</p>' },
  { id:'a-007', date:'2026-05-14', category:'Research', author:'Public Health Institute', tags:['EWARS+','climate','forecasting'],
    title:'EWARS+ climate-driven forecasting model goes live',
    summary:'EWARS+ goes live: weekly woreda-level alarm probabilities for cholera, malaria and dengue, 2 to 3 weeks ahead.',
    body:'<p>The Public Health Institute, in partnership with EPHI, has formally deployed <strong>EWARS+</strong>, an extension of the WHO EWARS framework that integrates rainfall, temperature, and humidity data with surveillance reporting.</p>'
        +"<p>The tool was built around a simple problem. Several of Ethiopia's heaviest disease burdens, cholera, malaria and dengue among them, rise and fall with the weather, yet surveillance data on its own tells responders where an outbreak already is rather than where it is about to be. EWARS+ is an attempt to buy those responders a little more time.</p>"
        +"<p>The model produces weekly alarm probabilities at woreda level for cholera, malaria, dengue, and meningitis. Probabilities are published every Monday and feed directly into PHEM's weekly bulletin.</p>"
        +"<p>Its inputs combine climate readings with the weekly IDSR reports that woredas already submit, drawn together through EPHI's data systems. Because it leans on reporting that is part of routine practice, the model can run every week without asking field teams to collect anything new.</p>"
        +'<p>Initial validation shows a lead time of 2-3 weeks ahead of outbreak confirmation, with sensitivity 78% and specificity 84% across the 2024-2025 retrospective dataset.</p>'
        +'<p>The Institute is careful about what the tool is and is not. An alarm is a probability, not a forecast of certainty, and it is meant to prompt a field team to look harder at a woreda rather than to trigger a response on its own. Sound epidemiological judgement and verification on the ground stay firmly in the loop.</p>'
        +'<p>Rollout is phased, beginning with training for regional epidemiologists on how to read and act on the weekly outputs. Later releases are expected to add further climate-sensitive conditions, with continued technical support from WHO as the model is tested against each new season.</p>' },
  { id:'a-008', date:'2026-05-06', category:'Programme', author:'MNCH Directorate', tags:['MNCH','ANC4','PNC1'],
    title:'MNCH continuum of care quality improvement collaborative',
    summary:'QI collaborative across 12 regions and 280 health centres, tracking the ANC1, ANC4, SBA and PNC1 continuum.',
    body:'<p>The MNCH Directorate is launching a quality-improvement collaborative across <strong>12 regions and 280 health centres</strong> to address gaps in the continuum of maternal care.</p>'
        +'<p>The problem it takes on is well known. Many women attend an early antenatal visit but do not return for the full schedule, some deliver without a skilled birth attendant, and others are never seen for a postnatal check in the critical days after birth. Each drop-off along that path carries risk for mother and newborn alike.</p>'
        +'<p>Cohort tracking will follow women from ANC1 through PNC1, with monthly facility-level review meetings. The intervention package includes training, mentoring visits, supply support, and digital tracking via DHIS2.</p>'
        +'<p>The collaborative works by connecting these facilities into a shared learning system. Teams at each health centre test small changes to their own workflow, measure whether appointments are kept and referrals completed, and bring the results to periodic learning sessions where sites compare what worked. Mentors visit between sessions to help teams turn ideas into routine practice.</p>'
        +"<p>The community side is just as important as the clinic. Health extension workers identify pregnant women early, encourage them to book antenatal care and follow up those who miss a visit, while pregnant women's conferences and local referral and transport arrangements help close the gap between a woman's home and a safe delivery.</p>"
        +'<p>The target outcome: increase ANC4 completion from 62% to 75%, and PNC1-within-48h from 48% to 65% by end of 2026.</p>'
        +'<p>The work is being carried out with Regional Health Bureaus and supported by partners including WHO and UNICEF, drawing on DHIS2 for its measurement. The first learning session is planned for the coming quarter, and the intention is to spread the methods that prove themselves to further health centres once the approach is tested.</p>' },
  { id:'a-009', date:'2026-04-29', category:'Press', author:'FMoH State Minister', tags:['donors','PEPFAR','HIV'],
    title:'Ethiopia signs renewed HIV programme cooperation with PEPFAR',
    summary:'Ethiopia renews a five-year PEPFAR agreement (2026 to 2030) targeting 95-95-95 and ending mother-to-child transmission.',
    body:"<p>Ethiopia's renewed cooperation with PEPFAR (President's Emergency Plan for AIDS Relief) covers the period 2026-2030, with a programmatic focus on reaching the 95-95-95 targets, eliminating mother-to-child transmission, and integrating HIV services into primary care.</p>"
        +'<p>The renewal comes on the back of steady progress. Ethiopia has driven new infections and AIDS-related deaths down over the past decade, and its epidemic is now concentrated among specific populations and locations rather than spread evenly across the country. That shape guides where the next five years of effort must go.</p>'
        +'<p>The agreement carries technical and financial support for ART scale-up, supply-chain strengthening, and the second-line ART transition.</p>'
        +'<p>Programme priorities include reaching groups the response has struggled to hold, particularly men and young people, and expanding differentiated service delivery so that stable clients can collect several months of medicine at once and refill closer to home. Routine viral-load testing anchors the effort to keep people not just on treatment but virally suppressed.</p>'
        +'<p>Current cascade indicators: 89% awareness, 78% on ART, 89% virally suppressed.</p>'
        +'<p>Ending mother-to-child transmission runs as a distinct track, delivered through antenatal care so that every pregnant woman is offered testing, treatment for those living with HIV, and early diagnosis and follow-up for their infants. Closing the remaining paediatric gap is one of the clearest tests of the new plan.</p>'
        +'<p>Both sides framed the agreement around sustainability. Alongside donor support, the FMoH is working towards greater domestic financing and the integration of HIV care into the wider primary health system, so that services endure as external funding evolves and national ownership grows.</p>' },
  { id:'a-010', date:'2026-04-21', category:'Advisory', author:'PHEM', tags:['rainy season','flooding'],
    title:'Pre-rainy-season preparedness advisory',
    summary:"PHEM's annual pre-rainy-season advisory: cholera, malaria, dengue and post-flood risks ahead of Kiremt (June to September).",
    body:'<p>The Public Health Emergency Management directorate has issued its annual pre-rainy-season preparedness advisory ahead of the <strong>Kiremt season (June-September)</strong>.</p>'
        +'<p>The rains reshape the disease landscape each year. Flooding contaminates wells and springs, standing water gives mosquitoes new breeding ground, and displacement crowds families into conditions where illness spreads easily. Preparedness ahead of the peak is far cheaper, in lives and in resources, than response once an outbreak has started.</p>'
        +'<p>Anticipated risks: water-borne outbreaks (cholera, AWD), vector-borne diseases (malaria, dengue), and post-flood public-health hazards in lowland regions.</p>'
        +'<p>On the water-borne front, the directorate is checking that oral cholera vaccine can be requested quickly where risk is highest, that WASH supplies and household water treatment are in place, and that treatment centres can be stood up at short notice in the woredas with a history of seasonal cholera.</p>'
        +'<p>For malaria, the emphasis falls on the lowland and returning-endemic areas, where insecticide-treated nets, indoor residual spraying and pre-positioned rapid diagnostic tests and artemisinin-based treatments need to be in place before transmission climbs rather than after.</p>'
        +'<p>Regional Health Bureaus are advised to verify pre-positioned stock of ORS, RDTs, ITNs, and IRS supplies, and to confirm functional incident command structures by 1 June.</p>'
        +'<p>Surveillance ties it together. Weekly reporting and early-warning tools are being watched closely for the first signs of a rise, health extension workers are briefed to spot and report clusters in their communities, and the directorate is coordinating with the water and disaster-management sectors so that a flood alert and a health alert reach the same table.</p>' },
];

export const ARTICLE_CATEGORIES = ['PHEM', 'Press', 'Advisory', 'Programme', 'Research'] as const;
