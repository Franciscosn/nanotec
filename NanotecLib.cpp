///////////////////////////////////////////////////////////////////////////////////////////////////
//
// NanotecLib.cpp: implementation of the CNanotecLib class.
//
///////////////////////////////////////////////////////////////////////////////////////////////////


#include "NanotecLib.h"
#include <iostream>
#include <iomanip>

using namespace std;


/////////////////////////////////// construction/destruction //////////////////////////////////////

	////Calibration[0] = 100.0;
	////Calibration[1] = 100.0;
	////Encoder_calibration[0] = -1.32;
	////Encoder_calibration[1] = -3.3;
	//Encoder_position_mm[0] = 0.0;
	//Encoder_position_mm[1] = 0.0;
	//motor_rest_time_sec[0] = 0;
	//motor_rest_time_sec[1] = 0;
	
	//	if (act_step_mode[motor_number] == 0)
	//		pos = fabs(Target_position_mm[motor_number]) * 10000.0 / Calibration[motor_number];
	//	else
	//		pos = fabs(Target_position_mm[motor_number]) * 10000.0 * act_step_mode[motor_number] / Calibration[motor_number];
				
	//Actual_position_mm[0] = ipos / (10000.0 * act_step_mode[0]) * Calibration[0];
	//   ipos = (int)(Target_position_mm[motor_number] * 10000 * act_step_mode[motor_number] / Calibration[motor_number]);

	// initial_position[motor_number] = index;
    // Actual_position_mm[motor_number] = index / (10000.0 * act_step_mode[motor_number]) * Calibration[motor_number];


CNanotecLib::CNanotecLib()
{
	pRS485 = 0;
	dwBoardAdr = 1;
	sAdr = "1";
	sFirmware = "Simulation";
	pStatusStr = new string("");

	dwMaxSteps = 230000;
	dwVRef = dwV = 1000;
	bEncoderInverted = false;

	bReferenced = false;

	dwSteps = 0;
	bForward = true;
	bParametersToBeReset = false;

	iNewPos = iCurrentPos = iLastPos = iEncoderPos = 0;

	bRunning = false;
	dwNoOfRuns = dwCurrentRun = 0;
	iBeginRun = iEndRun = 0;

	iSimDeltaPos = 100;
}


CNanotecLib::~CNanotecLib()
{
	if ( pStatusStr != 0 )
		delete pStatusStr;
}


////////////////////////// init/close/reset of the Nanotec ////////////////////////////////////////


//-------------------------------------------------------------------------------------------------
/// Init of the Nanotec interface.
/// Parameters: 'i_pRS485' pointer to the RS485-interface, 'i_MaxSteps' is the maximum number of 
/// steps (>= 10!) and 'i_EncoderInverted' does invert the direction for the encoder interprtation.
///
DWORD CNanotecLib::Init( CRS485Lib *i_pRS485, const DWORD i_BoardAdr, const DWORD i_MaxSteps, const bool i_EncoderInverted )
{
	if ( i_BoardAdr == 0 )
		return NanotecLib_Init | ES_OutOfRange;
	if ( const DWORD error = SetMaxSteps( i_MaxSteps ) )
		return error;
	bEncoderInverted = i_EncoderInverted;
	if ( !SIM )
	{
		if ( const DWORD error = CBoardLib::Init( i_pRS485, i_BoardAdr ) )
			return error;
		//if ( i_pRS485 == 0 )
		//	return NanotecLib_Init | ES_OutOfRange;
		//if ( !i_pRS485->GetInit() )
		//	return NanotecLib_Init | EH_NotInitialized;
	}
	//if ( i_Adr == 0 )
	//	return NanotecLib_Init | ES_OutOfRange;

	pRS485 = i_pRS485;
	pStream->str("");
	*pStream << dec << dwBoardAdr;
	sAdr = pStream->str();

	iNewPos = iCurrentPos = iLastPos = 0;

	// read the firmware version of the Nanotec interface
	if ( !SIM )
		if ( const DWORD error = Read( "v", &sFirmware ) )
		{
			CBoardLib::Close();
			return error;
		}

	// Satz aus Eprom lesen
	if ( const DWORD error = Write( "y1" ) )
	{
		CBoardLib::Close();
		return error;
	}
	if ( const DWORD error = ResetParameters() )
	{
		CBoardLib::Close();
		return error;
	}
	// Satz im Eprom speichern
	if ( const DWORD error = Write( ">1" ) )
	{
		CBoardLib::Close();
		return error;
	}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// Reset the positioning mode, the direction and other parameters the Nanotec interface.
///
DWORD CNanotecLib::ResetParameters()
{
	// Phasenstrom in % einstellen
	if ( const DWORD error = Write( "i", 10 ) )
		return error;
	// Phasenstrom im Stillstand in % einstellen
	if ( const DWORD error = Write( "r", 1 ) )
		return error;

	// Relative Positionierung, da die Steuerung ansonsten keine Wiederholfahrten unterstützt!
	if ( const DWORD error = Write( "p1" ) )
	    return error;

	// Startgeschwindigkeit
	if ( const DWORD error = Write( "u", 50 ) )
		return error;
	// finale Fahrgeschwindigkeit
	if ( const DWORD error = Write( "o", dwV ) )
		return error;

	// Beschleunigungsrampe: Hz/ms = (3000.0 / sqrt((float)<parameter>)) - 11.7
	if ( const DWORD error = Write( "b", ( 3000 / ( 3 ) - 12 ) ) )
		return error;
	// Bremsrampe = 0 <=> Beschleunigungsrampe
	if ( const DWORD error = Write( "B0" ) )
		return error;

	// Drehrichtung rechts in Richtung des Coaters
	if ( const DWORD error = Write( "d1" ) )
	    return error;
	dwSteps = 0;
	bForward = true;

	// Bei Wiederholungen wird die Drehrichtung des Motors bei jeder Wiederholung umgedreht.
	if ( const DWORD error = Write( "t1" ) )
	    return error;
	// ein Zyklus, keine Wiederholung
	if ( const DWORD error = Write( "W1" ) )
	    return error;

	// Fehlerkorrektur deaktivieren
	if ( const DWORD error = Write( "U0" ) )
	    return error;
	// Encoderrichtung einstellen
	if ( const DWORD error = Write( "q", bEncoderInverted ) )
		return error;

	bRunning = false;
	bParametersToBeReset = false;
	return ReadIsReferenced( &bReferenced );
}


//-------------------------------------------------------------------------------------------------
/// Reset of the Nanotec interface.
/// There is no possibility to program the Nanotec interface to a certain position. So, e.g. after a
/// crash, it is necessary to make a reference turn. Calling 'Reset()' does not change the internal
/// position counter of the Nanotec-interface.
///
DWORD CNanotecLib::Reset()
{
	if ( const DWORD error = Stop() )
		return error;
	if ( const DWORD error = CBoardLib::Reset() )
		return error;

	_MySleep( pRS485->GetWaitBus() );	// give time to the Nanotec interface
	return ResetParameters();
}


//-------------------------------------------------------------------------------------------------
//
string CNanotecLib::GetFirmwareVersion()
{
	return sFirmware;
}


/////////////////////// configuration / status of the Nanotec interface ///////////////////////////


////-------------------------------------------------------------------------------------------------
///// Get address on the RS485-bus.
/////
//DWORD CNanotecLib::GetAdr() const
//{
//	return dwAdr;
//}


//-------------------------------------------------------------------------------------------------
/// Get maximum number of steps.
///
DWORD CNanotecLib::GetMaxSteps() const
{
	return dwMaxSteps;
}


//-------------------------------------------------------------------------------------------------
/// Set maximum number of steps (>=10 !).
///
DWORD CNanotecLib::SetMaxSteps( const DWORD i_MaxSteps )
{
	if ( (i_MaxSteps < 10) )
		return NanotecLib_SetMaxSteps | ES_OutOfRange;

	dwMaxSteps = i_MaxSteps;
	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// Is the direction for the encoder interprtation inverted?
///
bool CNanotecLib::GetEncoderInverted() const
{
	return bEncoderInverted;
}


//-------------------------------------------------------------------------------------------------
/// Get the current velocity [steps/sec] in case of reference move (homing).
///
DWORD CNanotecLib::GetVRef() const
{
	return dwVRef;
}


//-------------------------------------------------------------------------------------------------
/// Set the velocity (>= 1 step/sec) in case of reference move (homing).
///
DWORD CNanotecLib::SetVRef( const DWORD i_VRef )
{
	if ( (i_VRef < 10) || (i_VRef > dwMaxSteps) )
		return NanotecLib_SetVRef | ES_OutOfRange;

	dwVRef = i_VRef;
	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// Get the velocity [steps/sec].
///
DWORD CNanotecLib::GetV() const
{
	return dwV;
}


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::SetV( const DWORD i_V )
{
	if ( (i_V < 1) || (i_V > dwMaxSteps) )
		return NanotecLib_SetV | ES_OutOfRange;
	else
		dwV = i_V;

	return Write( "o", dwV );
}


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::ReadFullConfig( std::string* pSetStr )
{
	if ( !SIM )
		if ( const DWORD error = Read( "|", pSetStr ) )
			return error;

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
//
string CNanotecLib::GetStatusStr()
{
	return *pStatusStr;
}


// bool positioning_error
//-------------------------------------------------------------------------------------------------
// 
DWORD CNanotecLib::ReadStatus( bool *pMovingFinished )
{
	string result = "", err = "";

	if ( SIM )
	{
		if ( iCurrentPos == iNewPos )
		{
			if ( iCurrentPos == 0 && bReferenced )
				result = "163";
			else
				result = "161";
		}
		else
			result = "160";
	}
	else
		if ( const DWORD error = Read( "$", &result ) )
		    return error;
		
	const DWORD status = (DWORD) strtol( result.c_str(), NULL, 10 );
	*pStatusStr = "";
	if ( ( status & 0xf0) == 0xa0 )
	{
		if ( status & 0x01 )
		{
			*pStatusStr = "Idle"; 
			*pMovingFinished = true;
			bRunning = false;
		}
		else
		{
			*pStatusStr = "Moving"; 
			*pMovingFinished = false;
		}
		if ( status & 0x02 )
		{
			*pStatusStr += " at Reference";
			bReferenced = true;
		}
		if ( status & 0x04 )
		{
			*pStatusStr += " with Position Error";
			*pMovingFinished = true;
			bRunning = false;
			err = "e";
		}
		if ( status & 0x08 )
		{
			*pStatusStr += " ext. ctrl. delayed";
			err = "e";
		}
	}
	else
	{
		*pStatusStr = "Status is invalid";
		*pMovingFinished = true;
		bRunning = false;
	}

	pStream->str("");
	*pStream << " (0x" << hex << setw(2) << status << ")";
	*pStatusStr += pStream->str();

	if ( err == "e" )
	{
		if ( const DWORD error = ReadErrorMemory( &err ) )
			return error;
		*pStatusStr += "\n" + err;
	}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::ReadErrorMemory( std::string *pErrorStr )
{
	string result = "0";
	if ( !SIM )
		if ( const DWORD error = Read( "E", &result ) )
			return error;
	const DWORD memory_index = (DWORD) strtol( result.c_str(), NULL, 10 );

	pStream->str("");
	*pStream << dec << memory_index << "E";
	if ( const DWORD error = Read( pStream->str(), &result ) )
	    return error;
	const DWORD error_code = (DWORD) strtol( result.c_str(), NULL, 10 );
	
	*pErrorStr = "";
	if ( error_code & 0x01 ) *pErrorStr += " Error low voltage";
	if ( error_code & 0x02 ) *pErrorStr += " Error temperature";
	if ( error_code & 0x04 ) *pErrorStr += " Error current shut-down";
	if ( error_code & 0x08 ) *pErrorStr += " Error in eeprom";
	if ( error_code & 0x10 ) *pErrorStr += " Error position";
	if ( error_code & 0x20 ) *pErrorStr += " Error controller";
	if ( error_code & 0x80 ) *pErrorStr += " Error driver";

	pStream->str("");
	*pStream << " (0x" << hex << setw(2) << error_code << ")";
	*pErrorStr += pStream->str();
	return EC_OK;
}


////////////////////////////////////// Position data //////////////////////////////////////////////


//-------------------------------------------------------------------------------------------------
/// Get the new intended position for the step motor relative to zero position.
///
int CNanotecLib::GetNewPosition() const
{
	return iNewPos;
}


//-------------------------------------------------------------------------------------------------
/// Get the current position of stepper motor from the Nanotec interface relative to zero position.
/// There is no active position measurement system in Nanotec systems.
///
DWORD CNanotecLib::ReadCurrentPosition( int *pPos )
{
	if ( !SIM )
	{
		string result = "";
		if ( const DWORD error = Read( "C", &result ) )
		   return error;

		*pPos = (int) strtol( result.c_str(), NULL, 10 );

		// Leider gibt Nanotec keinerlei Informationen zum Wiederholungsstaus heraus.
		// Diese muss man sich hier durch die Historie der Positionsveränderungen selbst erschließen!
		if ( bRunning )
		{
			if ( bForward )
			{
				if ( iCurrentPos >= *pPos )
				{
					dwCurrentRun++;
					bForward = false;
					iLastPos = iEndRun;
					iNewPos = iBeginRun;
				}
			}
			else
				if ( iCurrentPos <= *pPos )
				{
					dwCurrentRun++;
					bForward = true;
					iLastPos = iBeginRun;
					iNewPos = iEndRun;
				}

			if ( dwCurrentRun == dwNoOfRuns )
			{
				bRunning = false;
				bParametersToBeReset = true;
			}
		}

		iCurrentPos = *pPos;
	}
	else
	{
		//if ( bForward )
		//{
		//	if ( iCurrentPos + iSimDeltaPos >= iNewPos )
		//		*pPos = iCurrentPos = iNewPos;
		//	else
		//		*pPos = iCurrentPos = iCurrentPos + iSimDeltaPos;
		//}
		//else
		//	if ( iCurrentPos + iSimDeltaPos <= iNewPos )
		//		*pPos = iCurrentPos = iNewPos;
		//	else
		//		*pPos = iCurrentPos = iCurrentPos + iSimDeltaPos;

		//if ( bReferenceActive )
		//	if ( iCurrentPos == 0 )
		//		bReferenced = true;

		//if ( bRunning )
		//	if ( iCurrentPos == iNewPos )
		//		if ( dwCurrentRun == dwNoOfRuns )
		//			bRunning = false;
		//		else
		//		{
		//			dwCurrentRun++;
		//			iLastPos = iCurrentPos;
		//			iSimDeltaPos = -iSimDeltaPos;
		//			bForward = !bForward;
		//			if ( bForward )
		//				iNewPos = dwSteps + iCurrentPos;
		//			else
		//				iNewPos = iCurrentPos - dwSteps;
		//		}
	}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// Get the last position of the step motor relative to zero position.
///
int CNanotecLib::GetLastPosition() const
{
	return iLastPos;
}


//-------------------------------------------------------------------------------------------------
/// Set the nominal position of the step motor.
///
void CNanotecLib::SetNewPosition( const int i_Pos )
{
	iNewPos = i_Pos;
}


//-------------------------------------------------------------------------------------------------
/// Get 
///
DWORD CNanotecLib::ReadEncoder( int *pPos )
{
	if ( !SIM )
	{
		string result = "";
		if ( const DWORD error = Read( "I", &result ) )
		   return error;

		*pPos = (int) strtol( result.c_str(), NULL, 10 );

		//// Leider gibt Nanotec keinerlei Informationen zum Wiederholungsstaus heraus.
		//// Diese muss man sich hier durch die Historie der Positionsveränderungen selbst erschließen!
		//if ( bRunning )
		//{
		//	if ( bForward )
		//	{
		//		if ( iCurrentPos >= *pPos )
		//		{
		//			dwCurrentRun++;
		//			bForward = false;
		//			iLastPos = iEndRun;
		//			iNewPos = iBeginRun;
		//		}
		//	}
		//	else
		//		if ( iCurrentPos <= *pPos )
		//		{
		//			dwCurrentRun++;
		//			bForward = true;
		//			iLastPos = iBeginRun;
		//			iNewPos = iEndRun;
		//		}

		//	if ( dwCurrentRun == dwNoOfRuns )
		//	{
		//		bRunning = false;
		//		bParametersToBeReset = true;
		//	}
		//}

		iEncoderPos = *pPos;
	}
	else
	{
		//if ( bForward )
		//{
		//	if ( iCurrentPos + iSimDeltaPos >= iNewPos )
		//		*pPos = iCurrentPos = iNewPos;
		//	else
		//		*pPos = iCurrentPos = iCurrentPos + iSimDeltaPos;
		//}
		//else
		//	if ( iCurrentPos + iSimDeltaPos <= iNewPos )
		//		*pPos = iCurrentPos = iNewPos;
		//	else
		//		*pPos = iCurrentPos = iCurrentPos + iSimDeltaPos;

		//if ( bReferenceActive )
		//	if ( iCurrentPos == 0 )
		//		bReferenced = true;

		//if ( bRunning )
		//	if ( iCurrentPos == iNewPos )
		//		if ( dwCurrentRun == dwNoOfRuns )
		//			bRunning = false;
		//		else
		//		{
		//			dwCurrentRun++;
		//			iLastPos = iCurrentPos;
		//			iSimDeltaPos = -iSimDeltaPos;
		//			bForward = !bForward;
		//			if ( bForward )
		//				iNewPos = dwSteps + iCurrentPos;
		//			else
		//				iNewPos = iCurrentPos - dwSteps;
		//		}
	}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::GetCurrentRun() const
{
	return dwCurrentRun;
}


/////////////////////////////////// Reference the step motor //////////////////////////////////////


//-------------------------------------------------------------------------------------------------
/// Get if the Nanotec interface and its motor have performed succesfully reference move (homing).
///
bool CNanotecLib::GetIsReferenced() const
{
	return bReferenced;
}


//-------------------------------------------------------------------------------------------------
/// Read out if the Nanotec interface and its motor have performed succesfully reference move (homing).
///
DWORD CNanotecLib::ReadIsReferenced( bool *pIsRef )
{
	if ( SIM )
		*pIsRef = bReferenced;
	else
	{
		string result = "";
		if ( const DWORD error = Read( ":is_referenced", &result ) )
		    return error;

		bReferenced = strtol( result.c_str(), NULL, 10 ) == 0 ? false : true;
		*pIsRef = bReferenced;
	}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// Initiate a reference move (homing).
///
DWORD CNanotecLib::StartReference( const DWORD i_VRef )
{
	if ( DWORD error = SetVRef( i_VRef ) )
		return error;
	// Startgeschwindigkeit
	if ( const DWORD error = Write( "u", dwVRef ) )
		return error;
	// finale Fahrgeschwindigkeit
	if ( const DWORD error = Write( "o", dwVRef ) )
	    return error;
	bParametersToBeReset = true;

	iLastPos = iCurrentPos;

	DWORD e1 = EC_OK, e2 = EC_OK, e3 = EC_OK, e4 = EC_OK;
	// Drehrichtung links in Richtung des Schleusenflanges
	if ( !(e1 = Write( "d0" )) )
	{
		bForward = false;
		// ein Zyklus, keine Wiederholung
		if ( !(e2 = Write( "W1" )) )
			// externe Referenzfahrt
			if ( !(e3 = Write( "p4" )) )
				e4 = Write( "A" );
	}
	if ( e1 )
		return e1;
	else if ( e2 )
		return e2;
	else if ( e3 )
		return e3;
	else if ( e4 )
		return e4;

	iNewPos = 0;
	dwSteps = 0;
	bReferenced = false;

	if ( SIM )
	{
//		if ( iCurrentPos >= 0 )
//		{
//			bForward = false;
////			iSimDeltaPos = min( -100, -(dwVRef/10) );
//		}
//		else
//		{
//			bForward = true;
////			iSimDeltaPos = max( 100, iVRef/10 );
//		}

	}

	return EC_OK;
}


/////////////////////////////// move the Nanotec step motor ///////////////////////////////////////


//-------------------------------------------------------------------------------------------------
/// Move to absolute Position P [steps].
///
DWORD CNanotecLib::MoveAbs( const int P, const DWORD i_V )
{
	// Da 'iCurrentPos' asynchron und in parallel neu gelesen worden sein kann, 
	// kann das hier falsch berechnet werden! (Programmspeicher nötig!)
	return MoveRel( P - iCurrentPos, i_V );
}


//-------------------------------------------------------------------------------------------------
/// Relative movement to next position with steps S [steps].
///
DWORD CNanotecLib::MoveRel( int S, const DWORD i_V )
{
	if ( bReferenced )
		if ( (iCurrentPos + S > ((int) dwMaxSteps)) || (iCurrentPos + S < -((int) dwMaxSteps)) )
			return NanotecLib_MoveRel | ES_OutOfRange;
	if ( i_V != dwV )
		if ( const DWORD error = SetV( i_V ) )
			return error;
	if ( bParametersToBeReset )
		if ( const DWORD error = ResetParameters() )
			return error;

	// Da 'iCurrentPos' asynchron und in parallel neu gelesen worden sein kann, 
	// kann 'iLastPos' hier falsch berechnet werden! (Programmspeicher nötig!)
	iLastPos = iCurrentPos;

	if ( S == 0 )
	{
		dwSteps = 0;
		return EC_OK;
	}

	// Da 'iCurrentPos' asynchron und in parallel neu gelesen worden sein kann, 
	// kann 'iNewPos' hier falsch berechnet werden! (Programmspeicher nötig!)
	iNewPos = iCurrentPos + S;

	if ( S > 0 )
	{
		dwSteps = (DWORD) S;
		bForward = true;
		if ( const DWORD error = Write( "d1" ) )
			return error;
		//iSimDeltaPos = max( 100, iV/10 );
	}
	else
	{
		dwSteps = (DWORD) (-S); 
		bForward = false;
		if ( const DWORD error = Write( "d0" ) )
			return error;
		//iSimDeltaPos = min( -100, -iV/10 );
	}

	if ( const DWORD error = Write( "s", dwSteps ) )
		return error;
	else
		return Write( "A" );
}


//-------------------------------------------------------------------------------------------------
/// Stop immediately the step motor.
///
DWORD CNanotecLib::Stop()
{
	if ( const DWORD error = Write( "S" ) )
	    return error;
	bRunning = false;

	if ( SIM )
		iSimDeltaPos = 0;
	else
	{
		if ( const DWORD error = ReadCurrentPosition( &iCurrentPos ) )
			return error;
		if ( const DWORD error = ReadEncoder( &iEncoderPos ) )
			return error;
	}

	iNewPos = iCurrentPos;
	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
/// 
///
DWORD CNanotecLib::Run( const int i_Begin, const int i_End, const DWORD i_Runs, const DWORD i_V )
{
	if ( (i_Begin == i_End) || i_Runs == 0 )
		return NanotecLib_Runs | ES_OutOfRange;
	else
	{
		dwNoOfRuns = i_Runs;
		dwCurrentRun = 0;
	}

	if ( const DWORD error = ReadIsReferenced( &bReferenced ) )
		return error;
	if ( !bReferenced )
		return NanotecLib_Runs | EH_NotInitialized;

	if ( const DWORD error = MoveAbs( i_Begin, i_V ) )
		return error;

	// Anzahl der Wiederholungen = Runs
	if ( const DWORD error = Write( "W", dwNoOfRuns ) )
		return error;

	iBeginRun = i_Begin;
	iEndRun = i_End;

	if ( const DWORD error = MoveRel( i_End - i_Begin, i_V ) )
		return error;

	bRunning = true;
	dwCurrentRun = 1;
	bParametersToBeReset = true;
	return EC_OK;
}


/////////////////////////// single read / write transactions //////////////////////////////////////


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::Read( std::string Cmd, std::string *pResult )
{
	if ( !bInitialized )
		return NanotecLib_Read | ES_NotInitialized;

	string command = sAdr + "Z" + Cmd + "\r";
	if ( const DWORD error = pRS485->Write( "#" + command ) )
		return error;
	if ( const DWORD error = pRS485->Read( pResult ) )
		return error;

	if ( Cmd == "$" )
	{
		// '00 + sAdr + Cmd' is expected to be returned as the first part of the answer.
		if ( pResult->substr( 0, 2+sAdr.length()+1 ) != ("00" + sAdr + Cmd) )
			return NanotecLib_Read | EH_InvalidResponse;

		pResult->erase( 0, 2+sAdr.length()+1 );
	}
	else
		if ( Cmd == "|" )
		{
			// 'sAdr + "Z" is expected to be returned as the first part of the answer.
			if ( ( pResult->substr( 0, sAdr.length() + 1 ) ) != command.substr( 0, sAdr.length() + 1 ) )
				return NanotecLib_Read | EH_InvalidResponse;

			pResult->erase( 0, command.length() - 2 );
		}
		else
		{
			// 'sAdr + "Z" + Cmd' is expected to be returned as the first part of the answer.
			if ( ( pResult->substr( 0, command.length() - 1 ) + "\r" ) != command )
				return NanotecLib_Read | EH_InvalidResponse;

			pResult->erase( 0, command.length() - 1 );
		}

	return EC_OK;
}


//-------------------------------------------------------------------------------------------------
//
DWORD CNanotecLib::Write( std::string Cmd, const DWORD Par )
{
	if ( !bInitialized )
		return NanotecLib_Write | ES_NotInitialized;

	if ( !SIM )
	{
		pStream->str("");
		if ( Par != 0xfffffff )
			*pStream << dec << Par;

		string command = sAdr + Cmd + pStream->str() + "\r";
		if ( const DWORD error = pRS485->Write( "#" + command ) )
			return error;
		string answer = "";
		if ( const DWORD error = pRS485->Read( &answer ) )
			return error;
	
		// '?' indicates unknown cmd or syntax error.
		if ( answer.find( "?") != string::npos )
			return NanotecLib_Write | ES_SyntaxError;
		// The full 'Command' is expected to be returned.
		if ( answer.substr( 0, command.length() ) != command )
			return NanotecLib_Write | EH_InvalidResponse;
	}

	return EC_OK;
}


///////////////////////////////////// error handling //////////////////////////////////////////////


//-------------------------------------------------------------------------------------------------
/// Fills 'pClassAndMethodName' with the name of the class and the method the 'MethodId' belongs to.
///
bool CNanotecLib::GetClassAndMethod( const DWORD MethodId, std::string *pClassAndMethodName )
{
	bClassIdAlreadyChecked = true;
	if ( (MethodId & EC_Mask) == EC_NanotecLib )
	{
		*pClassAndMethodName = "NanotecLib." + GetMethodName( MethodId ) + ": ";
		bClassIdAlreadyChecked = false;
		return true;
	}

	bClassIdAlreadyChecked = true;
	if ( CBoardLib::GetClassAndMethod( MethodId, pClassAndMethodName ) )
	{
		*pClassAndMethodName = "NanotecLib\n" + *pClassAndMethodName;
		return true;
	}

	return false;
}


//-------------------------------------------------------------------------------------------------
/// Returns the name of the method with Id 'MethodId'.
///
string CNanotecLib::GetMethodName( const DWORD MethodId )
{
	switch ( MethodId )
	{
		case NanotecLib_Init:			return "Init()";
		case NanotecLib_Close:			return "Close()";
		case NanotecLib_Reset:			return "Reset()";
		case NanotecLib_SetMaxSteps:	return "SetMaxSteps()";
		case NanotecLib_SetVRef:		return "SetVRef()";
		case NanotecLib_SetV:			return "SetV()";
		case NanotecLib_MoveAbs:		return "MoveAbs()";
		case NanotecLib_MoveRel:		return "MoveRel()";
		case NanotecLib_Runs:			return "Runs()";
		case NanotecLib_Write:			return "Write()";
		case NanotecLib_Read:			return "Read()";

		default:	return CErrorLib::GetMethodName( MethodId );
	}
}

