// CNanotecCtrlDlg.cpp : implementation file
//

#include "StdAfx.h"
#include "NanotecCtrlDlg.h"
//#include "NanotecLib.h"

#ifdef _DEBUG
#define new DEBUG_NEW
#undef THIS_FILE
static char THIS_FILE[] = __FILE__;
#endif


/////////////////////////////////////////////////////////////////////////////
// CNanotecCtrlDlg dialog


CNanotecCtrlDlg::CNanotecCtrlDlg(CWnd* pParent /*=NULL*/)
	: CDialog(CNanotecCtrlDlg::IDD, pParent)
{
	//{{AFX_DATA_INIT(CNanotecCtrlDlg)
		// NOTE: the ClassWizard will add member initialization here
	sFirmware[0] = sFirmware[1] = "";
	//}}AFX_DATA_INIT
}


void CNanotecCtrlDlg::DoDataExchange( CDataExchange* pDX )
{
	CDialog::DoDataExchange(pDX);
	//{{AFX_DATA_MAP(CNanotecCtrlDlg)
	DDX_Text( pDX, IDC_NANOTEC_CTRL_DLG_FIRMWARE1, sFirmware[0] );
	DDX_Text( pDX, IDC_NANOTEC_CTRL_DLG_FIRMWARE2, sFirmware[1] );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_SET_DELAYTIME, dwSetDelayTime );
	//DDV_MinMaxInt( pDX, dwSetDelayTime, 1, 255 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_DELAYTIME, dwGetDelayTime );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_SET_VOLTAGE0, iSetVoltage0 );
	//DDV_MinMaxInt( pDX, iSetVoltage0, 0, 5000 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_SET_VOLTAGE1, iSetVoltage1 );
	//DDV_MinMaxInt( pDX, iSetVoltage1, 0, 5000 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP0, dwSetVoltageRamp0 );
	//DDV_MinMaxInt(pDX, dwSetVoltageRamp0, 2, 255 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP1, dwSetVoltageRamp1 );
	//DDV_MinMaxInt( pDX, dwSetVoltageRamp1, 2, 255 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_VOLTAGE0, iGetVoltage0 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_VOLTAGE1, iGetVoltage1 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_VOLTAGERAMP0, dwGetVoltageRamp0 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_VOLTAGERAMP1, dwGetVoltageRamp1 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_CURRENT1, dwCurrent1 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_CURRENT0, dwCurrent0 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_STATUSWORD_0, sStatusWord0 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_STATUSCODE_0, sStatusCode0 );
	//DDX_Text( pDX, IDC_NHQ_CTRL_DLG_GET_STATUSWORD_1, sStatusWord1 );
	//}}AFX_DATA_MAP
}


BEGIN_MESSAGE_MAP(CNanotecCtrlDlg, CDialog)
	//{{AFX_MSG_MAP(CNanotecCtrlDlg)
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_SET_HV, OnNhqCtrlDlgSetHV )
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_GET_HV, OnNhqCtrlDlgGetHV )
	//ON_WM_TIMER()
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_RESET_Nhq, OnNhqCtrlDlgResetNhq )
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_TAKE, OnNhqCtrlDlgTake )
	//ON_WM_CTLCOLOR()
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_SHUTDOWN, OnNhqCtrlDlgShutdown )
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_OK, OnNhqCtrlDlgOk )
	//ON_BN_CLICKED( IDC_NHQ_CTRL_DLG_CANCEL, OnNhqCtrlDlgCancel )
	//}}AFX_MSG_MAP
END_MESSAGE_MAP()

/////////////////////////////////////////////////////////////////////////////
// CNanotecCtrlDlg message handlers

////-------------------------------------------------------------------------------------------------
////
//BOOL CNanotecCtrlDlg::OnInitDialog()
//{
//	CDialog::OnInitDialog();
//
//	return TRUE;
//}

//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgSetHV() 
//{
//	UpdateData( TRUE );
//
//	if ( bFirstTime )
//		if ( (iGetVoltage0 < iSetVoltage0 - iHVRange) || (iGetVoltage0 > iSetVoltage0 + iHVRange) ||
//			 (iGetVoltage1 < iSetVoltage1 - iHVRange) || (iGetVoltage1 > iSetVoltage1 + iHVRange) ||
//			 (dwSetVoltageRamp0 > 30) || (dwSetVoltageRamp1 > 30) )
//		{
//			CString str;
//			str = "This is your first check here after (re-)starting CDT Detector Control!\n";
//			str += "Do you really want to set these high voltage values?";
//			DWORD result = MessageBox( str, NULL, MB_OKCANCEL );
//			if ( result == IDCANCEL ) return;
//		}
//
//	SetStatusBar( 10, "writing to Nhq..."  );
//
//	if ( dwError = theApp.Nhq.SetDelayTime( dwSetDelayTime ) )
//		MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );	
//	SetStatusBar( 20, "writing to Nhq..."  );
//
//	if ( wChannelList & 0x01 )
//	{
//		if ( dwError = theApp.Nhq.SetVoltageRamp( 0, dwSetVoltageRamp0 ) )
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );		
//		SetStatusBar( 30, "writing to Nhq..."  );
//		if ( dwError = theApp.Nhq.SetVoltage( 0, iSetVoltage0 ) )
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );			
//	}
//
//	SetStatusBar( 40, "writing to Nhq..." );
//
//	if ( wChannelList & 0x02 )
//	{
//		if ( dwError = theApp.Nhq.SetVoltageRamp( 1, dwSetVoltageRamp1 ) )
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );		
//		SetStatusBar( 50, "writing to Nhq..."  );
//		if ( dwError = theApp.Nhq.SetVoltage( 1, iSetVoltage1 ) )
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );			
//	}
//
//	SetStatusBar( 0 );
//	RedrawWindow();			// Neuzeichnen des Dialogs
//	OnNhqCtrlDlgGetHV();
//	bFirstTime = false;
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgGetHV() 
//{
//	CString button_text;
//	int a;
//
//	GetDlgItem( IDC_NHQ_CTRL_DLG_GET_HV )->GetWindowText( button_text );
//
//	if ( button_text == "Get HV" )		// Auslesen der eingestllten Spannungen
//	{
//		GetDlgItem( IDC_NHQ_CTRL_DLG_GET_HV )->SetWindowText( "Reading HV" );
//
//		GetDlgItem( IDC_NHQ_CTRL_DLG_TAKE )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_HV )->EnableWindow( FALSE );
////		GetDlgItem( IDC_NHQ_CTRL_DLG_CHANNEL0 )->EnableWindow( FALSE );
////		GetDlgItem( IDC_NHQ_CTRL_DLG_CHANNEL1 )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_DELAYTIME )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGE0 )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP0 )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGE1 )->EnableWindow( FALSE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP1 )->EnableWindow( FALSE );
//
//		bTimerActive = TRUE;
//		if ( NhqCtrlDlgGetHV() ) a=0;
//		if ( bTimerActive )
//			SetTimer( ID_SPANNUNG_TIMER, 1500, NULL );
//	}
//	else
//	{
//		bTimerActive = FALSE;
//		KillTimer( ID_SPANNUNG_TIMER );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_GET_HV )->SetWindowText( "Get HV" );
//
//		GetDlgItem( IDC_NHQ_CTRL_DLG_TAKE )->EnableWindow( TRUE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_HV )->EnableWindow( TRUE );
////		GetDlgItem( IDC_NHQ_CTRL_DLG_CHANNEL0 )->EnableWindow( TRUE );
////		GetDlgItem( IDC_NHQ_CTRL_DLG_CHANNEL1 )->EnableWindow( TRUE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_DELAYTIME )->EnableWindow( TRUE );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGE0 )->EnableWindow( wChannelList & 0x01 );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP0 )->EnableWindow( wChannelList & 0x01 );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGE1 )->EnableWindow( wChannelList & 0x02 );
//		GetDlgItem( IDC_NHQ_CTRL_DLG_SET_VOLTAGERAMP1 )->EnableWindow( wChannelList & 0x02 );
//	}
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//int CNanotecCtrlDlg::NhqCtrlDlgGetHV() 
//{
//	SetStatusBar( 10, "asking Nhq..." );
//
//	dwSetDelayTime = theApp.Nhq.GetNominalDelayTime();
//	if ( dwError = theApp.Nhq.GetActualDelayTime( &dwGetDelayTime ) ) 	
//		MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//
//	SetStatusBar( 20, "asking Nhq..." );
//
//	string str="";
//	DWORD status_code = 0;
//	SetStatusBar( 25, "asking Nhq..." );
//	if ( dwError = theApp.Nhq.GetStatusCode( 0, &str, &status_code ) ) 	
//		MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//	sStatusCode0 = str.c_str();
//
//	if ( status_code & 0x08 )
//		wChannelList &= ~0x01;
//	else
//		wChannelList |= 0x01;
//
//	if ( wChannelList & 0x01 )
//	{
//		SetStatusBar( 30, "asking Nhq..." );
//		if ( dwError = theApp.Nhq.GetStatusWord( 0, &str ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		sStatusWord0 = str.c_str();
//	}
//
//	SetStatusBar( 35, "asking Nhq..." );
//	dwError = theApp.Nhq.GetStatusCode( 1, &str, &status_code );
//
//	// Test, ob der 2. HV-Kanal überhaupt existiert.
//	if ( dwError ) 
//	{
//		if ( !(dwError & ES_OutOfRange) )
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//	}
//	else
//	{
//		sStatusCode1 = str.c_str();
//		if ( status_code & 0x08 )
//			wChannelList &= ~0x02;
//		else
//			wChannelList |= 0x02;
//
//		if ( wChannelList & 0x02 )
//		{
//			SetStatusBar( 40, "asking Nhq..." );
//			if ( dwError = theApp.Nhq.GetStatusWord( 1, &str ) ) 	
//				MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//			sStatusWord1 = str.c_str();
//		}
//	}
//
//	theApp.Nhq.SetChannelOn_Off( wChannelList );
//
//	if ( wChannelList & 0x01 )
//	{
//		if ( dwError = theApp.Nhq.GetNominalVoltageRamp( 0, &dwSetVoltageRamp0 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//
//		if ( dwError = theApp.Nhq.GetNominalVoltage( 0, &iSetVoltage0 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//
//		if ( dwError = theApp.Nhq.GetActualVoltageRamp( 0, &dwGetVoltageRamp0 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		SetStatusBar( 50, "asking Nhq..." );
//
//		if ( dwError = theApp.Nhq.GetActualVoltage( 0, &iGetVoltage0 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		SetStatusBar( 60, "asking Nhq..." );
//
//		if ( dwError = theApp.Nhq.GetCurrent( 0, &dwCurrent0 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//	}
//
//	if ( wChannelList & 0x02 )
//	{
//		if ( dwError = theApp.Nhq.GetNominalVoltageRamp( 1, &dwSetVoltageRamp1 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//
//		if ( dwError = theApp.Nhq.GetNominalVoltage( 1, &iSetVoltage1 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		SetStatusBar( 70, "asking Nhq..." );
//
//		if ( dwError = theApp.Nhq.GetActualVoltageRamp( 1, &dwGetVoltageRamp1 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		SetStatusBar( 80, "asking Nhq..." );
//
//		if ( dwError = theApp.Nhq.GetActualVoltage( 1, &iGetVoltage1 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//		SetStatusBar( 90, "asking Nhq..." );
//
//		if ( dwError = theApp.Nhq.GetCurrent( 1, &dwCurrent1 ) ) 	
//			MessageBox( theApp.Nhq.GetErrorText( dwError ).c_str(), NULL, MB_ICONERROR );
//	}
//
//
//	SetStatusBar( 0 );
//	return 0;
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgShutdown() 
//{
//	dwSetDelayTime = 1;
//	dwSetVoltageRamp0 = 25;
//	dwSetVoltageRamp1 = 25;
//	iSetVoltage0 = 0;
//	iSetVoltage1 = 0;
//
//	UpdateData( FALSE );	// Aktualisieren der Oberfläche
//	OnNhqCtrlDlgSetHV();
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgResetNhq() 
//{
//	if ( dwError = theApp.Nhq.Reset() )
//	{
//		CString str, text;
//		text += "Could not reset Nhq!\r\n" + (CString) theApp.Nhq.GetErrorText( dwError ).c_str();
//		MessageBox( text, NULL, MB_ICONERROR );
//	}
//
//	NhqCtrlDlgGetHV();
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgTake() 
//{
//	UpdateData( TRUE );		// Einlesen der Oberfläche
//
//	// take the actual values as set points
//	dwSetDelayTime = dwGetDelayTime;
//	dwSetVoltageRamp0 = dwGetVoltageRamp0;
//	dwSetVoltageRamp1 = dwGetVoltageRamp1;
//	iSetVoltage0 = iGetVoltage0;
//	iSetVoltage1 = iGetVoltage1;
//
//	UpdateData( FALSE );	// Aktualisieren der Oberfläche
//	RedrawWindow();			// Neuzeichnen des Dialogs
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgOk() 
//{
//	OnNhqCtrlDlgSetHV();	
//	CDialog::OnOK();
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::OnNhqCtrlDlgCancel() 
//{
//	CDialog::OnCancel();	
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//BOOL CNanotecCtrlDlg::DestroyWindow() 
//{
//	KillTimer( ID_SPANNUNG_TIMER );
//	return CDialog::DestroyWindow();
//}
//
//
////-------------------- Abarbeiten der Timer Events ----------------------------
////
//void CNanotecCtrlDlg::OnTimer( UINT_PTR nIDEvent ) 
//{
//	int a;
//
//	if ( nIDEvent == ID_SPANNUNG_TIMER )
// 	{
//		KillTimer( ID_SPANNUNG_TIMER );
//
//		if ( NhqCtrlDlgGetHV() ) a=0;		// Auslesen der eingestllten Spannungen
//		
//		if ( bTimerActive )
//			SetTimer( ID_SPANNUNG_TIMER, 1500, NULL );
// 	}
//	
//	CDialog::OnTimer( nIDEvent );	// Wenn der Timer nicht für diese View-Ebene war,
//}									// Timer-Event weiterreichen
//
//
////-------------------------------------------------------------------------------------------------
////
//HBRUSH CNanotecCtrlDlg::OnCtlColor( CDC* pDC, CWnd* pWnd, UINT nCtlColor ) 
//{
//	HBRUSH hbr = CDialog ::OnCtlColor( pDC, pWnd, nCtlColor );
//	
//	// TODO: Change any attributes of the DC here
//	switch ( nCtlColor )
//	{
//		case CTLCOLOR_STATIC:	{
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_SHUTDOWN )->m_hWnd == pWnd->m_hWnd )
//										pDC->SetTextColor( RGB(255, 0, 0) );
//
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_GET_VOLTAGE0 )->m_hWnd == pWnd->m_hWnd )
//										if ( (((int) iGetVoltage0) < ((int) iSetVoltage0) - ((int) iHVRange)) || 
//											 (iGetVoltage0 > iSetVoltage0 + iHVRange) )
//											pDC->SetTextColor( RGB(255, 0, 0) );
//
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_GET_VOLTAGERAMP0 )->m_hWnd == pWnd->m_hWnd )
//										if ( dwGetVoltageRamp0 != dwSetVoltageRamp0 )
//											pDC->SetTextColor( RGB(255, 0, 0) );
//
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_GET_VOLTAGE1 )->m_hWnd == pWnd->m_hWnd )
//										if ( (((int) iGetVoltage1) < ((int) iSetVoltage1) - ((int) iHVRange)) || 
//											 (iGetVoltage1 > iSetVoltage1 + iHVRange) )
//											pDC->SetTextColor( RGB(255, 0, 0) );
//
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_GET_VOLTAGERAMP1 )->m_hWnd == pWnd->m_hWnd )
//										if ( dwGetVoltageRamp1 != dwSetVoltageRamp1 )
//											pDC->SetTextColor( RGB(255, 0, 0) );
//
//									if ( GetDlgItem( IDC_NHQ_CTRL_DLG_GET_DELAYTIME )->m_hWnd == pWnd->m_hWnd )
//										if ( dwGetDelayTime != dwSetDelayTime )
//											pDC->SetTextColor( RGB(255, 0, 0) );
//
//									break;
//								}
//
//		default:			return CDialog::OnCtlColor( pDC, pWnd, nCtlColor );
//    }      
//         	
//	// TODO: Return a different brush if the default is not desired
//	return hbr;
//}
//
//
////-------------------------------------------------------------------------------------------------
////
//void CNanotecCtrlDlg::SetStatusBar( int Pos, CString Text ) 
//{
//	AfxGetMainWnd()->SendMessage( WM_UPDATE_STATUS_BAR, (WPARAM) &Pos, (LPARAM) &Text );
//	UpdateData( FALSE );		
//}
//
