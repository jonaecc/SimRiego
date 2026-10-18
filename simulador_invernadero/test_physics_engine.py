"""Pruebas de regresión del gemelo digital (sin navegador ni hardware)."""
import unittest

from physics_engine import GreenhouseEnvironment, NutrientSolution, transpiration_ml_min


class InitialStateTests(unittest.TestCase):
    def test_day_one_starts_at_lights_on_with_20_percent_vwc(self):
        env = GreenhouseEnvironment()
        self.assertAlmostEqual(env.sim_time_seconds, 6 * 3600.0)
        for p in env.plants:
            self.assertAlmostEqual(p.vwc, 20.0, delta=0.5)
            self.assertAlmostEqual(p.vwc_mean, 20.0, delta=0.5)


class AutomaticIrrigationTests(unittest.TestCase):
    def setUp(self):
        self.env = GreenhouseEnvironment()
        self.plant = self.env.plants[0]

    def test_p0_waits_after_lights_on_then_p1_can_start(self):
        self.env.sim_time_seconds = 6.5 * 3600
        self.plant.W = self.plant.pot_ml * 0.30
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertFalse(self.plant.disparo_activo)

        self.env.sim_time_seconds = 7.0 * 3600
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertTrue(self.plant.disparo_activo)
        self.assertEqual(self.plant.disparo_fase, "P1")

    def test_auto_off_closes_everything(self):
        self.env.sim_time_seconds = 8 * 3600.0  # pasado el P0 posterior al encendido
        self.plant.W = self.plant.pot_ml * 0.30
        self.env.step(0.05)
        self.assertTrue(self.env.pump_p1)
        self.env.set_auto_irrigation_mode(False)
        self.assertFalse(any(p.valve_cmd for p in self.env.plants))
        self.assertFalse(self.env.pump_p1)
        self.assertFalse(self.env.pump_p2)

    def test_p3_is_only_night_and_respects_its_safety_window(self):
        self.env.sim_time_seconds = 21.0 * 3600
        self.assertEqual(self.env.fase_riego_actual(), "ESPERA")

        self.env.sim_time_seconds = 3.5 * 3600
        self.plant.W = self.plant.pot_ml * 0.20
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertTrue(self.plant.disparo_activo)
        self.assertEqual(self.plant.disparo_fase, "P3")
        self.assertTrue(self.env.pump_p1, "El salvataje P3 comparte Bomba P1 en el firmware")

    def test_tray_capacity_is_configurable_and_blocks_irrigation(self):
        self.env.update_plant_config(0, {"capacidad_bandeja_ml": 100.0})
        self.assertEqual(self.plant.config.capacidad_bandeja_ml, 100.0)
        self.plant.W = self.plant.W_cc + 300.0
        self.plant.step(60.0, True, 1.0, 25.0, False, self.env.solution_p1, 1.0)
        self.assertTrue(self.plant.inundacion_critica)
        self.env.empty_tray_manually(0)
        self.env.tare_tray(0)
        self.env.hay_inundacion_critica()
        self.assertFalse(self.plant.inundacion_critica)

    def test_flood_blocks_all_irrigation_then_clears_when_sensor_is_safe_and_valve_is_closed(self):
        self.env.sim_time_seconds = 8 * 3600.0  # pasado el P0 posterior al encendido
        self.env.update_plant_config(0, {"capacidad_bandeja_ml": 100.0})
        self.plant.tray_ml = 120.0
        self.plant.W = self.plant.pot_ml * 0.30
        other = self.env.plants[1]
        other.disparo_activo = True
        other.disparo_fase = "P1"
        other.valve_cmd = True
        self.env.pump_p1 = True

        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertTrue(self.plant.inundacion_critica)
        self.assertFalse(any(p.disparo_activo for p in self.env.plants))
        self.assertFalse(self.env.pump_p1)
        self.assertTrue(self.env.tare_tray(0), "TA real acepta tara aun con agua")
        self.assertTrue(self.plant.tare_with_water)
        self.assertEqual(self.plant.tray_ml, 120.0)

        self.env.empty_tray_manually(0)
        self.env.tare_tray(0)
        self.env.hay_inundacion_critica()
        self.assertEqual(self.plant.tray_weight_ml, 0.0)
        self.assertFalse(self.plant.inundacion_critica)
        # El firmware conserva el intervalo entre disparos aun tras el corte.
        self.env.sim_time_seconds += self.plant.config.p1_tiempo_entre_min * 60.0
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertTrue(self.plant.disparo_activo)

    def test_tray_has_no_passive_drain_water_stays_until_emptied(self):
        """El drenaje es medible: solo desaparece al vaciar (manual o servicio fin de P2)."""
        self.plant.tray_ml = 800.0
        self.env.auto_irrigation_mode = False   # solo se observa la bandeja
        for _ in range(20):
            self.env.step(30.0)  # 10 h simuladas sin vaciado
        self.assertEqual(self.plant.tray_ml, 800.0)
        self.env.empty_tray_manually(0)
        self.assertEqual(self.plant.tray_ml, 0.0)
        self.assertEqual(self.plant.tray_tare_ml, 0.0, "vaciar a mano no modifica la tara")

    def test_optional_external_service_empties_then_tares_at_end_of_p2(self):
        self.env.set_auto_tray_service(True)
        self.plant.tray_ml = 350.0
        self.plant.tray_tare_ml = 0.0
        self.env.sim_time_seconds = 19.99 * 3600
        self.env.step(1.0)  # cruza las 20:00, fin de P2
        self.assertEqual(self.plant.tray_ml, 0.0)
        self.assertEqual(self.plant.tray_tare_ml, 0.0)
        self.assertFalse(self.plant.tare_with_water)

    def test_field_capacity_test_detects_first_drainage_and_derives_volume(self):
        self.env.sim_time_seconds = 6.5 * 3600  # P0: sin riego automático paralelo
        self.assertTrue(self.env.start_field_capacity_test(0, 3500, 200, 1))
        for _ in range(240):   # Más iteraciones para caudal bajo de plántula (1 gotero × 1.8 L/h)
            self.env.step(1.0)  # 60 s simulados por paso
            if self.plant.cc_result:
                break
        result = self.plant.cc_result
        self.assertIsNotNone(result)
        self.assertTrue(result["completed"])
        self.assertGreater(result["retained_ml"], 0.0)
        self.assertAlmostEqual(self.plant.config.p1_vol_cc, result["retained_ml"])
        self.assertAlmostEqual(self.plant.config.p2_vol_cc, result["retained_ml"])

    def test_field_capacity_test_preempts_an_active_automatic_shot(self):
        self.plant.disparo_activo = True
        self.plant.disparo_fase = "P1"
        self.plant.valve_cmd = True
        self.assertTrue(self.env.start_field_capacity_test(0, 3500, 150, 1))
        self.assertTrue(self.plant.cc_test)
        self.assertFalse(self.plant.disparo_activo)

    def test_observed_firmware_bw_can_calibrate_the_digital_twin(self):
        self.env.update_plant_config(0, {"pot_volume_ml": 5000, "vwc_cc": 42.0})
        self.assertEqual(self.plant.vwc_cc, 42.0)
        self.assertEqual(self.plant.config.p1_vol_cc, 2100.0)
        self.assertEqual(self.plant.config.p2_vol_cc, 2100.0)
        self.assertGreater(self.plant.config.tray_capacity_recommended_ml, self.plant.config.p2_runoff_max)

    def test_management_stages_keep_independent_pots_and_irrigation_profiles(self):
        self.assertEqual(self.plant.management_stage, "PLANTULA")
        self.assertEqual(self.plant.pot_ml, 500.0)
        self.env.update_plant_config(0, {"p1_ec_target": 1.1})
        self.assertTrue(self.env.set_management_stage(0, "CRECIMIENTO"))
        self.assertEqual(self.plant.pot_ml, 5000.0)
        self.assertNotEqual(self.plant.config.p1_ec_target, 1.1)
        self.assertTrue(self.env.set_management_stage(0, "PLANTULA"))
        self.assertEqual(self.plant.config.p1_ec_target, 1.1)

    def test_substrate_controls_virtual_field_capacity_and_shot_basis(self):
        self.assertTrue(self.env.set_substrate(0, "COCO"))
        coco_retained = self.plant.config.p1_vol_cc
        self.assertTrue(self.env.set_substrate(0, "SUELO"))
        self.assertEqual(self.plant.vwc_cc, 45.0)
        self.assertLess(self.plant.config.p1_vol_cc, coco_retained)

    def test_configurable_p0_blocks_before_its_end_then_allows_p1(self):
        self.env.p1_offset_min = 120.0
        self.env.sim_time_seconds = 7.5 * 3600
        self.plant.W = self.plant.pot_ml * 0.30
        self.assertEqual(self.env.fase_riego_actual(), "P0")
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertFalse(self.plant.disparo_activo)
        self.env.sim_time_seconds = 8.0 * 3600
        self.env.execute_automatic_irrigation_logic(1.0)
        self.assertTrue(self.plant.disparo_activo)

    def test_pumps_deliver_their_own_recipe(self):
        self.env.update_solution("P1", {"ec": 1.1, "ph": 5.5, "n": 90, "p": 30, "k": 120})
        self.env.update_solution("P2", {"ec": 3.0, "ph": 6.3, "n": 180, "p": 70, "k": 280})

        self.env.trigger_shot(0, duration_seconds=120.0)  # Manual = Bomba P1, como firmware
        before_ec, before_ph = self.plant.ec, self.plant.ph
        self.env.step(0.05)
        self.assertTrue(self.env.pump_p1)
        self.assertGreater(self.plant.total_irrigation_ml, 0.0)
        self.assertLess(self.plant.ec, before_ec)
        self.assertLess(self.plant.ph, before_ph)

        self.env.close_all_valves()
        self.env.set_management_stage(0, "CRECIMIENTO")
        self.env.sim_time_seconds = 12.0 * 3600
        self.plant.W = self.plant.pot_ml * 0.30
        self.plant.S = self.plant.W * 2.0  # EC bajo el objetivo P2: debe habilitar mantenimiento.
        self.plant.ultimo_disparo_sim_time = -99999.0
        ec_before_p2, ph_before_p2 = self.plant.ec, self.plant.ph
        self.env.step(0.05)
        self.assertTrue(self.env.pump_p2)
        self.assertGreater(self.plant.ec, ec_before_p2)
        self.assertGreater(self.plant.ph, ph_before_p2)

    def test_p2_hysteresis_stacks_shots_from_floor_up_to_max(self):
        """Tras tocar el piso, P2 sigue disparando (1 por intervalo) hasta el máximo."""
        self.env.set_management_stage(0, "CRECIMIENTO", update_env=True)
        self.env.update_plant_config(0, {"p2_vwc_min": 40.0, "p2_vwc_max": 59.0})
        self.env.time_speed = 1.0
        self.env.sim_time_seconds = 10.99 * 3600
        self.plant.W = self.plant.pot_ml * 0.405
        self.plant.ultimo_disparo_sim_time = -99999.0
        shots, was, vwc_max = 0, False, 0.0
        for _ in range(int(9.0 * 3600 / 15)):
            self.env.step(15)
            if self.env.fase_riego_actual() != "P2":
                continue
            act = self.plant.disparo_activo and self.plant.disparo_fase == "P2"
            shots += act and not was
            was = act
            vwc_max = max(vwc_max, self.plant.vwc)
        self.assertGreater(shots, 3, "debe apilar varios disparos, no uno por descenso al piso")
        self.assertGreater(vwc_max, 55.0, "debe subir hacia el máximo configurado")
        self.assertLessEqual(vwc_max, 59.5, "no debe pasarse del máximo (corte en pleno riego)")

    def test_ec_targets_and_substrate_ph_are_configurable(self):
        self.env.update_plant_config(0, {"p1_ec_target": 3.1, "p2_ec_target": 2.8, "substrate_ph": 6.2})
        cfg = self.plant.config
        self.assertEqual(cfg.p1_ec_target, 3.1)
        self.assertEqual(cfg.p2_ec_target, 2.8)
        self.assertEqual(self.plant.ph, 6.2)


class EvapotranspirationTests(unittest.TestCase):
    def _daily_et_ml(self, plant, hours_day=18.0):
        sol = GreenhouseEnvironment().solution_p1
        et0 = plant.total_et_ml
        for h in range(24):
            is_day = h < hours_day
            for _ in range(60):
                plant.W = max(plant.W, 0.55 * plant.pot_ml)   # sin estrés hídrico: mide la demanda
                plant.step(60.0, is_day, 1.1, 25.0, False, sol, 1.0,
                           ppfd=950.0 if is_day else 0.0, footprint_m2=0.25)
        return plant.total_et_ml - et0

    def test_transpiration_scales_with_plant_size_not_with_stage(self):
        env = GreenhouseEnvironment()
        small, big = env.plants[0], env.plants[1]
        for p in (small, big):
            env.update_plant_config(p.id, {"pot_volume_ml": 5000})
        small.biology.biomass_g, big.biology.biomass_g = 5.0, 100.0
        et_small, et_big = self._daily_et_ml(small), self._daily_et_ml(big)
        self.assertGreater(et_big, 5.0 * et_small)
        self.assertTrue(450.0 < et_big < 1100.0, f"planta vegetativa de 100 g: {et_big:.0f} mL/día")

    def test_seedling_uses_a_small_fraction_of_its_pot_per_day(self):
        env = GreenhouseEnvironment()
        seedling = env.plants[0]
        self.assertEqual(seedling.pot_ml, 500.0)
        self.assertLess(self._daily_et_ml(seedling), 0.15 * seedling.pot_ml)

    def test_mature_flowering_plant_has_high_daily_demand(self):
        env = GreenhouseEnvironment()
        p = env.plants[0]
        env.update_plant_config(0, {"pot_volume_ml": 15000})
        p.biology.biomass_g = 200.0
        et = self._daily_et_ml(p, hours_day=12.0)
        self.assertTrue(350.0 < et < 1000.0, f"floración madura: {et:.0f} mL/día")

    def test_transpiration_never_exceeds_the_available_energy(self):
        """λE por m² de suelo no puede superar la radiación absorbida más el término de VPD."""
        for ppfd in (600.0, 950.0, 1200.0):
            ml_min = transpiration_ml_min(5.0, 0.25, ppfd, 1.2, 25.5, 400.0, 0.03, True)
            w_m2 = ml_min / 1000.0 / 60.0 / 0.25 * 2.45e6
            self.assertLess(w_m2, ppfd / 4.57 * 0.85 + 120.0)

    def test_footprint_scales_water_use_per_plant(self):
        a = transpiration_ml_min(3.0, 0.25, 950.0, 1.2, 25.5, 400.0, 0.03, True)
        b = transpiration_ml_min(3.0, 0.50, 950.0, 1.2, 25.5, 400.0, 0.03, True)
        self.assertGreater(b, 1.3 * a)

    def test_leaf_area_grows_continuously_with_biomass(self):
        env = GreenhouseEnvironment()
        bio = env.plants[0].biology
        areas = []
        for grams in (2.0, 12.0, 42.0, 100.0, 238.0):
            bio.biomass_g = grams
            areas.append(bio.leaf_area_m2)
        self.assertEqual(areas, sorted(areas))
        self.assertLess(areas[0], 0.02)
        self.assertGreater(areas[-1], 1.0)

    def test_shot_volume_does_not_depend_on_the_time_step(self):
        """Un disparo de 120 s entrega lo mismo a 3 s de paso que a 300 s (antes: 2,5 veces más)."""
        volumes = []
        for dt in (3.0, 300.0):
            env = GreenhouseEnvironment()
            env.time_speed = 1.0
            p = env.plants[0]
            self.assertTrue(env.trigger_shot(0, duration_seconds=120.0))
            irr0 = p.total_irrigation_ml
            for _ in range(int(900 / dt)):
                env.step(dt)
            volumes.append(p.total_irrigation_ml - irr0)
        expected = p.config.flow_ml_min * p.dripper_variance * 2.0
        for v in volumes:
            self.assertAlmostEqual(v, expected, delta=0.08 * expected)


class TwoLayerPotTests(unittest.TestCase):
    def setUp(self):
        self.env = GreenhouseEnvironment()
        self.p = self.env.plants[0]
        self.env.update_plant_config(0, {"pot_volume_ml": 5000})
        self.sol = self.env.solution_p1

    def _run(self, minutes, is_day=True):
        for _ in range(int(minutes * 12)):
            self.p.step(5.0, is_day, 1.1, 25.0, False, self.sol, 1.0)

    def test_sensor_peak_after_irrigation_settles_as_water_moves_down(self):
        p = self.p
        p.W_t = p.vol_top_ml * 0.66
        p.W_b = p.vol_bottom_ml * 0.40
        top0, bot0 = p.vwc, p.vwc_bottom
        self._run(60, is_day=False)
        self.assertLess(p.vwc, top0 - 3.0, "el sensor debe caer tras el pico por redistribución")
        self.assertGreater(p.vwc_bottom, bot0 + 3.0, "la capa inferior debe ganar el agua que baja")

    def test_no_tray_runoff_until_the_bottom_layer_is_saturated(self):
        p = self.p
        p.W_t = p.vol_top_ml * 0.66
        p.W_b = p.vol_bottom_ml * 0.30
        self._run(30, is_day=False)
        self.assertEqual(p.tray_ml, 0.0)

    def test_saturated_pot_drains_to_the_tray(self):
        p = self.p
        p.W_t = p.vol_top_ml * 0.66
        p.W_b = p.vol_bottom_ml * 0.70
        self._run(60, is_day=False)
        self.assertGreater(p.tray_ml, 0.0)

    def test_top_dries_faster_than_bottom_under_light(self):
        p = self.p
        p.biology.biomass_g = 60.0
        p.W = p.pot_ml * 0.52
        self._run(180)
        self.assertLess(p.vwc, p.vwc_bottom - 0.5)

    def test_setting_total_water_keeps_both_layers_consistent(self):
        p = self.p
        p.W = p.pot_ml * 0.30
        self.assertAlmostEqual(p.vwc, 30.0, places=3)
        self.assertAlmostEqual(p.vwc_bottom, 30.0, places=3)
        self.assertAlmostEqual(p.vwc_mean, 30.0, places=3)


class CropPlanTests(unittest.TestCase):
    def setUp(self):
        self.env = GreenhouseEnvironment()

    def _make_ready(self, p, days, biomass):
        p.stage_start_day = p.biology.age_days - days
        p.biology.biomass_g = biomass

    def test_seedling_to_growth_transplants_without_touching_biology(self):
        p = self.env.plants[0]
        self._make_ready(p, 12.0, 9.0)
        bio_before = (p.biology.age_days, p.biology.biomass_g, p.biology.height_cm)
        self.env.review_crop_plan()
        self.assertEqual(p.management_stage, "CRECIMIENTO")
        self.assertEqual(p.pot_ml, 5000.0)
        self.assertEqual(bio_before, (p.biology.age_days, p.biology.biomass_g, p.biology.height_cm))
        self.assertEqual(self.env.crop_events[-1]["transplant"], True)
        self.assertFalse(self.env.crop_events[-1]["forced"])

    def test_needs_both_days_and_biomass(self):
        p = self.env.plants[0]
        self._make_ready(p, 5.0, 50.0)   # biomasa de sobra, pocos días
        self.env.review_crop_plan()
        self.assertEqual(p.management_stage, "PLANTULA")
        self._make_ready(p, 15.0, 3.0)   # días de sobra, poca biomasa
        self.env.review_crop_plan()
        self.assertEqual(p.management_stage, "PLANTULA")

    def test_max_days_forces_transition_and_flags_it(self):
        p = self.env.plants[0]
        self._make_ready(p, 22.0, 3.0)
        self.env.review_crop_plan()
        self.assertEqual(p.management_stage, "CRECIMIENTO")
        self.assertTrue(self.env.crop_events[-1]["forced"])

    def _all_to_growth(self):
        for p in self.env.plants:
            self._make_ready(p, 12.0, 9.0)
        self.env.review_crop_plan()

    def test_flowering_switch_waits_for_all_plants(self):
        self._all_to_growth()
        self.assertEqual(self.env.room_stage, "CRECIMIENTO")
        for p in self.env.plants[:-1]:
            self._make_ready(p, 30.0, 100.0)
        self._make_ready(self.env.plants[-1], 30.0, 20.0)
        self.env.review_crop_plan()
        self.assertEqual(self.env.room_stage, "CRECIMIENTO")
        self.assertTrue(all(p.management_stage == "CRECIMIENTO" for p in self.env.plants))
        self._make_ready(self.env.plants[-1], 30.0, 100.0)
        self.env.review_crop_plan()
        self.assertEqual(self.env.room_stage, "FLORACION")
        self.assertTrue(all(p.management_stage == "FLORACION" for p in self.env.plants))

    def test_flowering_keeps_pot_but_changes_irrigation_profile(self):
        self._all_to_growth()
        p = self.env.plants[0]
        ec_target_growth = p.config.p1_ec_target
        for q in self.env.plants:
            q.stage_profiles["FLORACION"]["pot_volume_ml"] = 5000.0
            self._make_ready(q, 30.0, 100.0)
        self.env.review_crop_plan()
        self.assertEqual(p.pot_ml, 5000.0)
        self.assertFalse(self.env.crop_events[-1]["transplant"])
        self.assertNotEqual(p.config.p1_ec_target, ec_target_growth)

    def test_larger_flowering_pot_means_transplant_and_pot_never_shrinks(self):
        self._all_to_growth()
        p = self.env.plants[0]
        p.stage_profiles["FLORACION"]["pot_volume_ml"] = 15000.0
        for q in self.env.plants:
            self._make_ready(q, 30.0, 100.0)
        self.env.review_crop_plan()
        self.assertEqual(p.pot_ml, 15000.0)
        p.stage_profiles["CRECIMIENTO"]["pot_volume_ml"] = 1000.0
        self.assertIsNone(p.advance_stage("FLORACION"))

    def test_any_plant_at_max_days_forces_room_switch(self):
        self._all_to_growth()
        for q in self.env.plants:
            self._make_ready(q, 30.0, 20.0)
        self._make_ready(self.env.plants[3], 50.0, 20.0)
        self.env.review_crop_plan()
        self.assertEqual(self.env.room_stage, "FLORACION")
        self.assertTrue(self.env.crop_events[-1]["forced"])

    def test_12_12_switch_induces_flowering_and_no_autoflower_by_age(self):
        p = self.env.plants[0]
        p.biology.age_days = 60.0
        for _ in range(20):
            self.env.step(15.0 / self.env.time_speed)
        self.assertFalse(p.biology.flower_induced)
        self._all_to_growth()
        for q in self.env.plants:
            self._make_ready(q, 30.0, 100.0)
        self.env.review_crop_plan()
        self.assertEqual((self.env.light_off_hour - self.env.light_on_hour) % 24.0, 12.0)
        for _ in range(20):
            self.env.step(15.0 / self.env.time_speed)
        self.assertTrue(p.biology.flower_induced)

    def test_crop_plan_validation_and_non_active_profile_edit(self):
        self.assertFalse(self.env.update_crop_plan("PLANTULA", min_days=30, max_days=10))
        self.assertFalse(self.env.update_crop_plan("FLORACION", min_days=1))
        self.assertTrue(self.env.update_crop_plan("PLANTULA", biomass_target_g=5))
        self.assertEqual(self.env.crop_plan["PLANTULA"]["biomass_target_g"], 5.0)
        self.assertTrue(self.env.update_stage_profile(0, "FLORACION", {"p1_ec_target": 3.3}))
        p = self.env.plants[0]
        self.assertEqual(p.management_stage, "PLANTULA")
        self.assertEqual(p.stage_profiles["FLORACION"]["p1_ec_target"], 3.3)


if __name__ == "__main__":
    unittest.main()


class SaltTransportTests(unittest.TestCase):
    def test_input_ec_up_to_15_is_accepted(self):
        env = GreenhouseEnvironment()
        env.update_solution("P2", {"ec": 10.0})
        self.assertEqual(env.solution_p2.ec, 10.0)

    def test_sensor_ec_rises_with_a_strong_shot_and_follows_the_top_layer(self):
        env = GreenhouseEnvironment()
        p = env.plants[0]
        ec0 = p.ec
        sol = NutrientSolution(ec=10.0, ph=6.0, n=100.0, p=50.0, k=150.0)
        p.valve_cmd = p.valve_open = True
        for _ in range(60):
            p.step(10.0, True, 1.1, 25.0, True, sol, 1.0)
        self.assertGreater(p.ec, ec0 + 1.0)
        self.assertGreater(p.ec, p.ec_bottom)   # el sensor ve más EC que la capa inferior

    def test_total_salt_is_conserved_without_drain_or_uptake(self):
        env = GreenhouseEnvironment()
        p = env.plants[0]
        p.W_t, p.W_b = 0.9 * p.vol_top_ml * p.vwc_cc / 100, 0.2 * p.vol_bottom_ml * p.vwc_cc / 100
        p.S_t, p.S_b = 5000.0, 1000.0
        s0 = p.S
        p.biology.biomass_g = 0.1
        p.step(600.0, False, 0.2, 20.0, False, env.solution_p2, 0.0)
        self.assertAlmostEqual(p.S, s0, delta=s0 * 0.02)
