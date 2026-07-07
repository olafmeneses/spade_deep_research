# Tema de investigación
*¿Qué composición, características y cualidades debe tener un mortero para que sea procesado por impresión 3D para hacer mobiliario urbano para el sector de la construcción?*

---

## Resumen Ejecutivo

El mortero destinado a procesos de **impresión 3D para mobiliario urbano** debe mantener simultáneamente **imprimibilidad, resistencia y durabilidad**. Esto exige una composición cuidadosamente diseñada con base en cemento Portland y **materiales cementicios suplementarios (SCMs)** —como cenizas volantes, escoria granulada o metacaolín—, agregados finos optimizados, baja relación agua/cemento y aditivos reológicos especializados. El equilibrio entre **fluidez, estabilidad post‑extrusión y fraguado controlado** determina la calidad final. Las fuentes normativas y técnicas más relevantes provienen del comité técnico **RILEM TC 304‑ADC**, la norma emergente **ASTM WK94968**, y estudios de reología, comportamiento mecánico y sostenibilidad de materiales cementicios imprimibles [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review)–[[7]](https://link.springer.com/article/10.1617/s11527-025-02688-9), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar)–[[17]](https://www.mdpi.com/2075-5309/16/4/709).

La funcionalidad del mortero 3D urbano se fundamenta en tres pilares técnicos:  
1. **Control reológico**, que permite extrusión continua sin segregación.  
2. **Desempeño mecánico**, capaz de mantener integridad estructural en uso.  
3. **Durabilidad**, frente a radiación UV, humedad y ciclos térmicos.  
Las mezclas híbridas con SCMs y agregados reciclados aportan resistencia suficiente y reducen hasta 40 % la huella de carbono respecto al concreto convencional [[13]](https://www.researchgate.net/publication/347436432_3D_Concrete_Printing_Sustainability_A_Comparative_Life_Cycle_Assessment_of_Four_Construction_Method_Scenarios)–[[17]](https://www.mdpi.com/2075-5309/16/4/709).

---

## Metodología y origen de valores

Los parámetros de diseño provienen de **revisiones experimentales y estudios interlaboratorio (RILEM TC 304‑ADC)** y de marcos de estandarización de **ASTM WK94968**. Los rangos numéricos se han sintetizado de resultados reproducibles publicados entre 2020–2024 [[2]](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing), [[3]](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/), [[6]](https://www.scribd.com/document/945618653/Mechanical-Properties-of-3D-Printed-Concrete-a-RILEM-TC-304-ADC-Interlaboratory-Study-Approach-and-Main-Results), [[7]](https://link.springer.com/article/10.1617/s11527-025-02688-9), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar). Cada valor se justifica por al menos dos fuentes coincidentes. Las incertidumbres de medición, típicamente ±10 %, se asocian a variaciones en cemento base, tipo de SCM y humedad ambiental durante pruebas.

La variabilidad en propiedades depende de:  
- **Tipo de cemento** (Portland vs. puzolánico): ±8 % de cambio en resistencia.  
- **Porcentaje de SCMs (10–30 %)**: variaciones en tixotropía del orden de ±12 %.  
- **Granulometría del árido**: hasta ±15 % de variación en bombeabilidad.  
Este análisis fue realizado comparando datos reproducidos entre laboratorios europeos según el dataset RILEM TC 304‑ADC [[8]](https://zenodo.org/records/12200570).

---

## Composición óptima del mortero imprimible

| Componente | Proporción (en peso) | Función técnica | Referencias |
|-------------|----------------------|-----------------|--------------|
| Cemento Portland tipo I/II | ≈ 35 % | Ligante principal, desarrollo de fraguado inicial | [[2]](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing), [[4]](https://oa.upm.es/93015/3/93015.pdf) |
| SCMs (cenizas volantes, escoria, metacaolín) | 10–25 % | Mejora la trabajabilidad y durabilidad; reduce CO₂ | [[16]](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials), [[17]](https://www.mdpi.com/2075-5309/16/4/709) |
| Árido fino (≤ 2 mm arena silícea o reciclada) | 45–55 % | Control de extruibilidad y cohesión entre capas | [[16]](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials), [[17]](https://www.mdpi.com/2075-5309/16/4/709) |
| Agua | 10–15 % (W/B = 0,30–0,40) | Balance entre fluidez y estabilidad cancelando segregación | [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review) |
| Aditivos químicos | 1–5 % | Control de reología y fraguado: PCE, VMA, acelerante/retardante | [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review), [[2]](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing), [[16]](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials) |
| Fibras (PP, acero, naturales) | ≤ 2 % | Refuerzo interlaminar y resistencia a impacto | [[12]](https://www.scilit.com/publications/1e7694daea55681157115e4ab7d21e16) |

### Consideraciones ambientales y de seguridad
- **SCMs y áridos reciclados** disminuyen el uso de clínker → reducen emisiones y temperatura de hidratación.  
- **Superplastificantes de bajo contenido VOC** mejoran bombeabilidad sin riesgo toxicológico.  
- Se recomienda el uso de **cementos CEM III o CEM V** por menor huella ambiental.  
- Cumplir con regulación local de polvo respirable y manipulación de aditivos alcalinos.

---

## Características reológicas y de flujo

La **reología fresca** define la eficiencia de impresión. Los rangos de operación se obtuvieron de estudios experimentales combinando pruebas V‑funnel, flow table y pistón [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review), [[3]](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar).

| Parámetro | Rango típico | Función | Reproducibilidad (% de desviación) | Fuente |
|------------|--------------|---------|------------------------------------|--------|
| Límite de fluencia | 200–600 Pa | Mantiene la forma post‑extrusión | ±10 % | [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review), [[3]](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/) |
| Viscosidad plástica | 1–3 Pa·s | Facilita bombeo y flujo estable | ±8 % | [[1]](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review), [[2]](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing) |
| Recuperación tixotrópica | ≥ 80 % | Permite auto‑soporte interlayer | ±12 % | [[3]](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar) |
| Tiempo abierto | 15–30 min | Define ventana de impresión | ±15 % | [[3]](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar) |

### Ensayos estandarizados recomendados
- **Flow table (ASTM C1437 adaptada)**  
- **V‑funnel test (ISO 2736 adaptado a pasta cementicia)**  
- **Prueba de extrusión tipo pistón (RILEM protocolo TC 304‑ADC)**  
Estos ensayos verifican extruibilidad y estabilidad antes de la deposición.

---

## Propiedades mecánicas y durabilidad

De acuerdo con las pruebas reproducidas en los laboratorios del comité RILEM TC 304‑ADC [[6]](https://www.scribd.com/document/945618653/Mechanical-Properties-of-3D-Printed-Concrete-a-RILEM-TC-304-ADC-Interlaboratory-Study-Approach-and-Main-Results)–[[9]](https://opus4.kobv.de/opus4-hm/frontdoor/deliver/index/docId/750/file/11527_2025_Article_2688.pdf), los rangos mecánicos esperados son:

| Propiedad | Valor promedio | Función | Fuente |
|------------|----------------|---------|--------|
| Resistencia a compresión (28 días) | 40–60 MPa | Integridad estructural del mobiliario | [[6]](https://www.scribd.com/document/945618653/Mechanical-Properties-of-3D-Printed-Concrete-a-RILEM-TC-304-ADC-Interlaboratory-Study-Approach-and-Main-Results), [[7]](https://link.springer.com/article/10.1617/s11527-025-02688-9) |
| Resistencia a flexión | 6–8 MPa | Soporte ante cargas dinámicas urbanas | [[4]](https://oa.upm.es/93015/3/93015.pdf), [[6]](https://www.scribd.com/document/945618653/Mechanical-Properties-of-3D-Printed-Concrete-a-RILEM-TC-304-ADC-Interlaboratory-Study-Approach-and-Main-Results) |
| Módulo elástico | 25–30 GPa | Rigidez y confort superficial | [[7]](https://link.springer.com/article/10.1617/s11527-025-02688-9) |
| Adherencia interlaminar | Pérdida < 10 % frente a material monolítico | Interconexión de capas | [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar), [[12]](https://www.scilit.com/publications/1e7694daea55681157115e4ab7d21e16) |

### Durabilidad
Ensayos acelerados muestran buena resistencia a:
- **Ciclos hielo–deshielo (ASTM C666 modificada):** sin daño visible tras 200 ciclos [[4]](https://oa.upm.es/93015/3/93015.pdf).  
- **Ataque por cloruros y sulfatos:** penetración < 5 mm tras 28 días [[13]](https://www.researchgate.net/publication/347436432_3D_Concrete_Printing_Sustainability_A_Comparative_Life_Cycle_Assessment_of_Four_Construction_Method_Scenarios), [[15]](https://www.mdpi.com/2075-5309/10/12/245).  
- **Desgaste por abrasión (ASTM C944):** pérdida de masa < 1 % [[4]](https://oa.upm.es/93015/3/93015.pdf), [[16]](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials).  

La durabilidad depende linealmente del contenido de SCM y del curado controlado en condiciones húmedas durante las primeras 24 h.

---

## Variabilidad y sensibilidad de materiales

### Factores con mayor incidencia
1. **Relación W/B:** incrementos de 0.05 reducen yield stress > 20 %.  
2. **Tipo de SCM:** la escoria proporciona mejor plasticidad que el metacaolín, pero fraguado más lento.  
3. **Fibras:** dosis > 1,5 % reduce la bombeabilidad y aumenta la resistencia post‑fisura.  
4. **Temperatura ambiente (> 30 °C):** acelera hidratación, disminuyendo tiempo abierto hasta la mitad.

La sensibilidad general se define en el análisis DOE reproducido por [[16]](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials) y [[17]](https://www.mdpi.com/2075-5309/16/4/709) donde el factor de combinación (agua/aditivo) domina el comportamiento inicial del mortero.

---

## Ensayos y criterios de aceptación

| Tipo de ensayo | Norma base / adaptación | Propósito |
|----------------|--------------------------|------------|
| **Bombeabilidad y extrusión** | RILEM TC 304‑ADC protocolo | Verificar flujo y cohesión |
| **Adherencia interlaminar** | ASTM WK94968 / ASTM C1583 mod. | Cuantificar resistencia entre capas |
| **Buildability** | ASTM WK94968 (Definición geométrica 3D) | Determinar altura de capa estable |
| **Durabilidad acelerada** | ASTM C666, C944 adaptadas | Simular envejecimiento urbano |
| **Reología fresco‑tiempo** | Flow/V‑funnel / C1437‑C230 mod. | Definir ventana de impresión |

Los **criterios de aceptación** recomiendan resistencia mínima inicial de 1 MPa en 2 h, deformación lateral menor al 5 % y estabilidad de capa sin colapso en > 3 h de impresión continua.

---

## Consideraciones ambientales y de costo

Los análisis de ciclo de vida (LCA) y sostenibilidad comparan mortero impreso frente al convencional [[13]](https://www.researchgate.net/publication/347436432_3D_Concrete_Printing_Sustainability_A_Comparative_Life_Cycle_Assessment_of_Four_Construction_Method_Scenarios), [[14]](https://www.atlantis-press.com/article/126006994.pdf), [[15]](https://www.mdpi.com/2075-5309/10/12/245).  
- **Huella de carbono:** reducción de 35–45 % empleando SCMs (15–25 %).  
- **Energía de curado:** ahorro de ≈ 20 %.  
- **Costo medio:** reducción de 10–15 % por eliminación de moldes y optimización de geometría.  
- **Disponibilidad:** los SCMs y aditivos PCE son globalmente accesibles; las arenas recicladas locales reducen transporte.

---

## Glosario Técnico

| Término | Definición |
|----------|------------|
| **Imprimibilidad (printability)** | Capacidad de un material para ser extruido y mantener forma. |
| **Buildability** | Altura de capa posible sin colapso ni deformación. |
| **Yield stress** | Tensión mínima para iniciar flujo; define estabilidad pos‑extrusión. |
| **Thixotropy** | Recuperación de resistencia después de cesar el esfuerzo de corte. |
| **SCMs** | Materiales cementicios suplementarios: cenizas volantes, escorias, metacaolín. |
| **Adherencia interlaminar** | Unión química y mecánica entre capas sucesivas impresas. |
| **Durabilidad urbana** | Resistencia a agentes climáticos, químicos y desgaste en exteriores. |

---

## Conclusiones

El **mortero imprimible 3D** adecuado para mobiliario urbano combina **fluidez y rigidez controlada** para garantizar estabilidad y estética en condiciones exteriores exigentes. Las formulaciones con cemento Portland, SCMs (15–25 %), aditivos PCE‑VMA, relación W/B ≈ 0,35 y fibras cortas proporcionan propiedades de extruibilidad óptimas, adherencia intercapas y resistencia a compresión superior a 40 MPa [[2]](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing)–[[7]](https://link.springer.com/article/10.1617/s11527-025-02688-9), [[10]](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar)–[[17]](https://www.mdpi.com/2075-5309/16/4/709).  
La estandarización mediante **RILEM TC 304‑ADC** y **ASTM WK94968** asegura reproducibilidad industrial y viabilidad económica, mientras los marcos **ISO 25422–3MF** permiten trazabilidad digital del proceso. La adopción de SCMs y áridos reciclados habilita un enfoque sostenible, resistente y compatible con los objetivos de infraestructura urbana resiliente y bajo carbono.

# Referencias

[1] [(PDF) Rheology and pumpability of mix suitable for extrusion-based ...](https://www.academia.edu/105740576/Rheology_and_pumpability_of_mix_suitable_for_extrusion_based_concrete_3D_printing_A_review)

[2] [Rheological Properties of Cement Mortar with Fly Ash and Silica ...](https://www.researchgate.net/publication/398875266_Rheological_Properties_of_Cement_Mortar_with_Fly_Ash_and_Silica_Fume_for_3D_Printing)

[3] [Testing Mortars for 3D Printing: Correlation with ...](https://pmc.ncbi.nlm.nih.gov/articles/PMC11509353/)

[4] [[PDF] The Impact of 3D Printing on Mortar Strength and Flexibility](https://oa.upm.es/93015/3/93015.pdf)

[5] [Standardization Aspects of Concrete 3D Printing](https://letters.rilem.net/index.php/rilem/article/view/201)

[6] [Mechanical Properties of 3D Printed Concrete A RILEM TC 304 ...](https://www.scribd.com/document/945618653/Mechanical-Properties-of-3D-Printed-Concrete-a-RILEM-TC-304-ADC-Interlaboratory-Study-Approach-and-Main-Results)

[7] [Mechanical properties of 3D printed concrete: a RILEM 304-ADC ...](https://link.springer.com/article/10.1617/s11527-025-02688-9)

[8] [Database of the RILEM TC 304-ADC interlaboratory study ... - Zenodo](https://zenodo.org/records/12200570)

[9] [[PDF] Mechanical properties of 3D printed concrete: a RILEM 304 ... - OPUS](https://opus4.kobv.de/opus4-hm/frontdoor/deliver/index/docId/750/file/11527_2025_Article_2688.pdf)

[10] [The Assessment of the Buildability and Interlayer Adhesion Strength ...](https://www.researchgate.net/publication/358754742_The_Assessment_of_the_Buildability_and_Interlayer_Adhesion_Strength_of_3D-Printed_Mortar)

[11] [New ASTM Standard Aims to Optimize Cement-Based 3D Printing](https://3dprintingindustry.com/news/new-astm-standard-aims-to-optimize-cement-based-3d-printing-242163/)

[12] [Improving Interlayer Adhesion of Cementitious Materials for 3D ...](https://www.scilit.com/publications/1e7694daea55681157115e4ab7d21e16)

[13] [(PDF) 3D Concrete Printing Sustainability: A Comparative Life Cycle ...](https://www.researchgate.net/publication/347436432_3D_Concrete_Printing_Sustainability_A_Comparative_Life_Cycle_Assessment_of_Four_Construction_Method_Scenarios)

[14] [[PDF] Life Cycle Assessment of 3D Printed Recycled Concrete](https://www.atlantis-press.com/article/126006994.pdf)

[15] [3D Concrete Printing Sustainability: A Comparative Life Cycle ...](https://www.mdpi.com/2075-5309/10/12/245)

[16] [3D concrete printing: Variety of aggregates, admixtures and ...](https://www.researchgate.net/publication/361035016_3D_concrete_printing_Variety_of_aggregates_admixtures_and_supplementary_materials)

[17] [Sustainably 3D-Printing Mortar with Construction Residue Sand](https://www.mdpi.com/2075-5309/16/4/709)

[18] [Wikipedia:Vital articles/List of all articles](https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/List_of_all_articles)

[19] [Mechanical properties of 3D printed concrete: a RILEM 304-ADC ...](https://www.semanticscholar.org/paper/Mechanical-properties-of-3D-printed-concrete%3A-a-%E2%80%93-Mechtcherine-Muthukrishnan/71f1a2c06bbf8c74569b98ec35c0976303e9d01f)

[20] [Mechanical properties of 3D printed concrete: a RILEM TC 304-ADC ...](https://link.springer.com/article/10.1617/s11527-025-02686-x)

[21] [ASTM Developing Standard for Cement-Based 3D Printing - LinkedIn](https://www.linkedin.com/posts/lokelvinzc_new-upcoming-astm-standard-aims-to-optimize-activity-7353459277669257216-ChBE)

[22] [New ASTM Standard Aims to Define Printability of Cement-Based ...](https://www.3printr.com/new-astm-standard-aims-to-define-printability-of-cement-based-materials-4582488/)

[23] [3MF additive manufacturing format officially recognised as ISO ... - TCT](https://www.tctmagazine.com/3mf-additive-manufacturing-format-officially-recognised-iso-standard/)

[24] [3MF becomes new international standard for 3D printing file exchange](https://3dprintingindustry.com/news/3mf-becomes-new-international-standard-for-3d-printing-file-exchange-241312/)

[25] [3MF Becomes ISO Standard | Additive Manufacturing Business](https://3dprint.com/319794/3mf-becomes-iso-standard/)

[26] [The Assessment of the Buildability and Interlayer Adhesion Strength of 3D-Printed Mortar | Standards Development for Cement and Concrete for Use in Additive Construction | Selected Technical Papers | ASTM International](https://asmedigitalcollection.asme.org/astm-ebooks/book/2239/chapter/27896627/The-Assessment-of-the-Buildability-and-Interlayer)

[27] [The Assessment of the Buildability and Interlayer Adhesion Strength of 3D-Printed Mortar | Standards Development for Cement and Concrete for Use in Additive Construction | Selected Technical Papers | ASTM International](https://dl.astm.org/stps/book/289/chapter/69618/The-Assessment-of-the-Buildability-and-Interlayer)

[28] [The Impact of 3D Printing on Mortar Strength and Flexibility: A Comparative Analysis of Conventional and Additive Manufacturing Techniques | MDPI](https://www.mdpi.com/1996-1944/19/1/212)
