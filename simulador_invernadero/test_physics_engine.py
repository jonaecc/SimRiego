"""Pruebas de regresión del gemelo digital (sin navegador ni hardware)."""
import unittest

from physics_engine import GreenhouseEnvironment


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

    def test_sil_passive_tray_drain_is_physical_not_automatic_tare(self):
        self.plant.tray_ml = 1800.0
        self.env.step(30.0)  # 30 s simulados: e^(-30/1800)
        self.assertLess(self.plant.tray_ml, 1800.0)
        self.assertEqual(self.plant.tray_tare_ml, 0.0)

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
        for _ in range(60):   # Más iteraciones para caudal bajo de plántula (1 gotero × 1.8 L/h)
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
        shots, was = 0, False
        for _ in range(int(9.0 * 3600 / 15)):
            self.env.step(15)
            if self.env.fase_riego_actual() != "P2":
                continue
            act = self.plant.disparo_activo and self.plant.disparo_fase == "P2"
            shots += act and not was
            was = act
        self.assertGreater(shots, 10, "debe apilar muchos disparos, no uno por descenso al piso")
        self.assertGreater(self.plant.vwc, 50.0, "debe subir hacia el máximo configurado")
        self.assertFalse(self.plant.p2_esperando_piso)

    def test_ec_targets_and_substrate_ph_are_configurable(self):
        self.env.update_plant_config(0, {"p1_ec_target": 3.1, "p2_ec_target": 2.8, "substrate_ph": 6.2})
        cfg = self.plant.config
        self.assertEqual(cfg.p1_ec_target, 3.1)
        self.assertEqual(cfg.p2_ec_target, 2.8)
        self.assertEqual(self.plant.ph, 6.2)


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
