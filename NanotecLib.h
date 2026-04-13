///////////////////////////////////////////////////////////////////////////////////////////////////
//
// NanotecLib.h: interface for the CNanotecLib class.
//
// ------------------------------------------------------------------------------------------------
//
// Description:
///                                                                                 \class CNanotecLib
/// 'CNanotecLib' allows easy handle of Nanotec step motors with controller of type 'SMCI47-S'.
//
// Please announce changes and hints to support@n-cdt.com
// Copyright (c) 2025 CDT GmbH
// All rights reserved.
//
/// ------------------------------------------------------------------------------------------------
//
// Redistribution and use in source and binary forms, with or without modification,
// are permitted provided that the following conditions are met:
//
// 1. Redistributions of source code must retain the above copyright notice,
//    this list of conditions and the following disclaimer.
// 2. Redistributions in binary form must reproduce the above copyright notice,
//    this list of conditions and the following disclaimer in the documentation
//    and/or other materials provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
// IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
// ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
// LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
// CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
// SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
// CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
// POSSIBILITY OF SUCH DAMAGE.
//
///////////////////////////////////////////////////////////////////////////////////////////////////


#ifndef NanotecLib_H
#define NanotecLib_H

#include "../BoardLib.h"
#include "../RS485Lib.h"


// error codes
const DWORD EC_NanotecLib			= 0x13000000;   // class id
const DWORD NanotecLib_Init			= 0x13010000;   // method id
const DWORD NanotecLib_Close		= 0x13020000;
const DWORD NanotecLib_Reset		= 0x13030000;
const DWORD NanotecLib_SetMaxSteps	= 0x13040000;
const DWORD NanotecLib_SetVRef		= 0x13050000;
const DWORD NanotecLib_SetV			= 0x13060000;
const DWORD NanotecLib_MoveAbs		= 0x13070000;
const DWORD NanotecLib_MoveRel		= 0x13080000;
const DWORD NanotecLib_Runs			= 0x13090000;
const DWORD NanotecLib_Write		= 0x130A0000;
const DWORD NanotecLib_Read			= 0x130B0000;

const bool SIM = false;	// simulate the Nanotec interface


class HL_API CNanotecLib : public CBoardLib
{

public:

	CNanotecLib();
	virtual ~CNanotecLib();

	// init/close/reset of the Nanotec interface

	virtual DWORD Init( CRS485Lib *i_pRS485, const DWORD i_BoardAdr, const DWORD i_MaxSteps = 1000, const bool i_EncoderInverted = false );
	DWORD ResetParameters();
	DWORD Reset();
	std::string GetFirmwareVersion();

	// configuration/status of the Nanotec interface

//	DWORD GetAdr() const;
	DWORD GetMaxSteps() const;
	DWORD SetMaxSteps( const DWORD i_MaxSteps );
	bool GetEncoderInverted() const;
	DWORD GetVRef() const;
	DWORD SetVRef( const DWORD i_VRef );
	DWORD GetV() const;
	DWORD SetV( const DWORD i_V );
	DWORD ReadFullConfig( std::string* pSetStr );

	std::string GetStatusStr();
	DWORD ReadStatus( bool *pMovingFinished );
	DWORD ReadErrorMemory( std::string *pErrorStr );

	// position data

	int GetNewPosition() const;
	DWORD ReadCurrentPosition( int *pPos );
	int GetLastPosition() const;
	void SetNewPosition( const int i_Pos );
	DWORD ReadEncoder( int *pPos );
	DWORD GetCurrentRun() const;

	// reference the step motor

	bool GetIsReferenced() const;
	DWORD ReadIsReferenced( bool *pIsRef );
	DWORD StartReference( const DWORD i_VRef );

	// move the Nanotec step motor

	DWORD MoveAbs( const int P, const DWORD i_V = 1000 );
	DWORD MoveRel( const int S, const DWORD i_V = 1000 );
	DWORD Stop();
	DWORD Run( const int i_Begin, const int i_End, const DWORD i_Runs, const DWORD i_V = 1000 );

	//  single read / write transactions 

	virtual DWORD Read( std::string Cmd, std::string *pResult );
	virtual DWORD Write( std::string Cmd, const DWORD Par = 0xfffffff );

	// error handling

	virtual bool GetClassAndMethod( const DWORD MethodId, std::string *pClassAndMethodName );

protected:

	// error handling

	std::string GetMethodName( const DWORD MethodId );

protected:

	CRS485Lib *pRS485;			// pointer to the RS485 interface used to control the Nanotec interface
//	DWORD dwAdr;				// address on the RS485-bus
	std::string sAdr;			// address on the RS485-bus as a string
	std::string *pStatusStr;	// status of the Nanotec interface
	std::string sFirmware;		// firmware version of the Nanotec interface

	DWORD dwMaxSteps;			// max. number of steps in one direction after a reference turn
	DWORD dwVRef;				// actual velocity of the step motor in case of reference turn
	DWORD dwV;					// actual velocity of the step motor
	bool bEncoderInverted;		// is the direction for the encoder interprtation inverted?

	int iNewPos;				// new intended position for the step motor relative to zero position
	int iCurrentPos;			// current position of the step motor relative to zero position
	int iLastPos;				// last position of the step motor relative to zero position
	int iEncoderPos;			// current position of the step motor relative to zero position

	bool bReferenced;			// are the Nanotec interface and its motor referenced (homing performed)?

	DWORD dwSteps;
	bool bForward;
	bool bParametersToBeReset;	// is there a reference move currently still active or not correctly finished?

	bool bRunning;				// 
	DWORD dwNoOfRuns;			// 
	DWORD dwCurrentRun;			//
	int iBeginRun, iEndRun;

	int iSimDeltaPos;			// 

};

#endif  // NanotecLib_H
