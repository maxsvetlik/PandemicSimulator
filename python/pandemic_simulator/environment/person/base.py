# Confidential, Copyright 2020, Sony Corporation of America, All rights reserved.
import dataclasses
from copy import deepcopy
from typing import Optional, List, Sequence, cast
from uuid import uuid4

import numpy as np

from ..interfaces import Person, PersonID, PersonState, LocationID, Risk, Registry, PandemicRegulation, \
    SimTime, NoOP, NOOP, SimTimeTuple, PandemicTestResult, ContactTracer
from ..location import Cemetery, Hospital

__all__ = ['BasePerson']


class BasePerson(Person):
    """Class that partially implements a sim person. """

    _id: PersonID
    _home: LocationID
    _registry: Registry
    _night_hours: SimTimeTuple
    _init_state: PersonState
    _numpy_rng: np.random.RandomState

    _state: PersonState
    _cemetery_ids: List[LocationID]
    _hospital_ids: List[LocationID]

    _regulation_compliance_prob: float

    def __init__(self, age: int,
                 home: LocationID,
                 registry: Registry,
                 name: Optional[str] = None,
                 risk: Optional[Risk] = None,
                 regulation_compliance_prob: float = 1.0,
                 night_hours: SimTimeTuple = SimTimeTuple(hours=tuple(range(0, 6)) + tuple(range(22, 24))),
                 init_state: Optional[PersonState] = None,
                 numpy_rng: Optional[np.random.RandomState] = None):
        """
        :param age: Age of the person
        :param home: Home location id
        :param registry: Registry instance to register the person and handle peron's entry to a location
        :param name: Optional name of the person
        :param risk: Optional health risk of the person
        :param night_hours: night hours - a person by default goes back home and stays at home
        :param regulation_compliance_prob: probability of complying to a regulation
        :param init_state: Optional initial state of the person
        :param numpy_rng: Random number generator
        """
        self._id = PersonID(name=name if name is not None else str(uuid4()), age=age)
        self._home = home
        self._registry = registry
        self._night_hours = night_hours
        self._numpy_rng = numpy_rng if numpy_rng is not None else np.random.RandomState()
        self._init_state = init_state or PersonState(infection_state=None,
                                                     current_location=home,
                                                     risk=risk if risk else self._numpy_rng.choice([r for r in Risk]))

        self._state = deepcopy(self._init_state)
        self._registry.register_person(self)
        self._cemetery_ids = self._registry.location_ids_of_type(Cemetery)
        self._hospital_ids = self._registry.location_ids_of_type(Hospital)

        self._regulation_compliance_prob = regulation_compliance_prob

    def enter_location(self, location_id: LocationID) -> bool:
        return self._registry.register_person_entry_in_location(self.id, location_id)

    @property
    def id(self) -> PersonID:
        return self._id

    @property
    def state(self) -> PersonState:
        return self._state

    @property
    def home(self) -> LocationID:
        return self._home

    @property
    def at_home(self) -> bool:
        """Return True if the person is at home and False otherwise"""
        return self._state.current_location == self.home

    @property
    def assigned_locations(self) -> Sequence[LocationID]:
        return self._home,

    def _sync(self, sim_time: SimTime) -> None:
        """Sync sim time specific variables. Subclasses must implement this"""
        raise NotImplementedError

    def _set_is_hospitalized(self, value: bool) -> None:
        inf_state_dict = dataclasses.asdict(self._state.infection_state)
        inf_state_dict['is_hospitalized'] = value
        self._state.infection_state = dataclasses.replace(self._state.infection_state, **inf_state_dict)

    def step(self, sim_time: SimTime, contact_tracer: Optional[ContactTracer] = None) -> Optional[NoOP]:
        # sync sim time specific variables
        self._sync(sim_time)
        self._registry.clear_quarantined(self._id)

        # the base person's policy includes whether to go to a hospital or a be transferred to a cemetery.
        curr_loc = self._state.current_location
        test_result = self._state.test_result
        is_hospitalized = self._state.infection_state is not None and self._state.infection_state.is_hospitalized
        if test_result == PandemicTestResult.DEAD:
            # the person is dead - if there is a cemetery and the person is not there then move the person there.
            if len(self._cemetery_ids) > 0 and curr_loc not in self._cemetery_ids:
                self.enter_location(self._cemetery_ids[self._numpy_rng.randint(0, len(self._cemetery_ids))])
                self._set_is_hospitalized(False)
            # nothing more to do since the person is dead - return None
            return None
        elif test_result == PandemicTestResult.CRITICAL:
            if len(self._hospital_ids) > 0:
                if not is_hospitalized:
                    # admit to a hospital
                    for _hosp_id in self._hospital_ids:
                        if self.enter_location(_hosp_id):
                            # admitted to a hospital
                            self._set_is_hospitalized(True)
                            # hospitalized - return None
                            return None
                    # if control reached here then all hospitals are full, try again in the next step
                else:
                    # already in a hospital, wait until recovered - return None
                    return None
        elif is_hospitalized:
            # recovered from illness
            self._set_is_hospitalized(False)
            # if in a hospital - go home
            if self._state.current_location in self._hospital_ids:
                self.enter_location(self.home)
                return None

        # block further ops for night hours by returning None
        if sim_time in self._night_hours:
            if not self.at_home:
                self.enter_location(self.home)
            return None

        comply_to_regulation = self._numpy_rng.uniform() < self._regulation_compliance_prob
        if (
                not self._registry.get_person_quarantined_state(self._id) and comply_to_regulation and

                # if you are tested sick and have to stay home
                (self._state.test_result in {PandemicTestResult.POSITIVE, PandemicTestResult.CRITICAL} and
                 not self.at_home and self._state.sick_at_home) or

                # quarantine
                (self._state.quarantine and not self.at_home) or

                # quarantine if household positive
                (self._state.quarantine_if_household_quarantined and not self.at_home and self._household_quarantined())
                or

                # quarantine if contact positive
                (contact_tracer is not None and self._state.quarantine_if_contact_positive and not self.at_home and
                 self._contact_positive(list(contact_tracer.get_contacts(self.id).keys())))
        ):
            self.enter_location(self.home)
            self._registry.quarantine_person(self._id)
            return None

        return NOOP

    def receive_regulation(self, regulation: PandemicRegulation) -> None:
        self._state.quarantine = regulation.quarantine
        self._state.quarantine_if_contact_positive = regulation.quarantine_if_contact_positive
        self._state.quarantine_if_household_quarantined = regulation.quarantine_if_household_quarantined
        self._state.sick_at_home = regulation.stay_home_if_sick
        self._state.avoid_gathering_size = regulation.risk_to_avoid_gathering_size[self._state.risk]
        self._state.avoid_location_types = (regulation.risk_to_avoid_location_types[self._state.risk]
                                            if regulation.risk_to_avoid_location_types is not None else [])

        self._state.infection_spread_multiplier = 0.8 if regulation.practice_good_hygiene else 1.0
        self._state.infection_spread_multiplier *= 0.6 if regulation.wear_facial_coverings else 1.0
        self._state.infection_spread_multiplier = (
                1 - (1 - self._state.infection_spread_multiplier) * self._regulation_compliance_prob)

    def _contact_positive(self, contacts: Sequence[PersonID]) -> bool:
        for contact in contacts:
            if self._registry.get_person_test_result(contact) in {PandemicTestResult.POSITIVE,
                                                                  PandemicTestResult.CRITICAL}:
                return True

        return False

    def _household_quarantined(self) -> bool:
        for hh in self._registry.get_households(self._id):
            if self._registry.get_person_quarantined_state(hh):
                return True

        return False

    def _get_social_gathering_location(self) -> Optional[LocationID]:
        # social gathering
        ags = self._state.avoid_gathering_size
        loc_ids = self._registry.location_ids_with_social_events
        num_events = len(loc_ids)
        comply_to_regulation = (self._numpy_rng.uniform() < self._regulation_compliance_prob)
        if num_events != 0:
            for i in self._numpy_rng.permutation(num_events):
                if (
                        ags == -1 or
                        not comply_to_regulation or
                        (comply_to_regulation and len(self._registry.get_persons_in_location(loc_ids[i])) < ags)
                ):
                    return cast(LocationID, loc_ids[i])
        return None

    def reset(self) -> None:
        self._state = deepcopy(self._init_state)
        self._registry.reassign_locations(self)
        self._registry.clear_quarantined(self._id)
        self.enter_location(self._state.current_location)
